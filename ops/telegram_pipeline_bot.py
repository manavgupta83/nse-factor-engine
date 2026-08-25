"""
Telegram bot — NSE pipeline trigger
Whitelists a single Telegram user ID. All other senders are silently ignored.

Commands:
  /start        — show help
  /run_pipeline — choose Rebalance or Monitor mode via buttons, then run
  /run_regime   — run regime_master.py (weekly HMM + liquidity risk)
  /status       — check if pipeline or regime is running
"""

import asyncio, signal, logging, os, re
from pathlib import Path
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ['TELEGRAM_BOT_TOKEN']
ALLOWED_USER_ID  = int(os.environ['TELEGRAM_USER_ID'])
BASE             = Path('/home/ec2-user/nse-factor-engine')
PIPELINE_SCRIPT  = BASE / 'run_pipeline.py'
REGIME_SCRIPT    = BASE / 'hmm-factor-engine' / 'regime' / 'regime_master.py'
FORMAT_PDF_CMD   = ['python3', str(BASE / 'ops' / 'format_portfolio_pdf.py')]
MKT_PDF_DIR      = BASE / 'market_movement' / 'data'
REGIME_PDF_DIR   = BASE / 'hmm-factor-engine' / 'regime' / 'data'
SIGNALS_DIR      = BASE / 'signals' / 'stage6'
PIPELINE_TIMEOUT = 3600
REGIME_TIMEOUT   = 3600
PARQUET_POLL_INTERVAL = 5
PARQUET_MAX_WAIT      = 300
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO,
    filename='/home/ec2-user/telegram_bot.log'
)

def is_allowed(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


# ── Helpers ───────────────────────────────────────────────────────────────────

async def wait_for_parquet(update):
    run_date_str = pd.Timestamp.now(tz='Asia/Kolkata').strftime('%d%m%Y')
    parquet_path = SIGNALS_DIR / f'portfolio_recommendations_{run_date_str}.parquet'
    elapsed = 0
    while elapsed < PARQUET_MAX_WAIT:
        if parquet_path.exists():
            logging.info(f'Parquet found after {elapsed}s: {parquet_path.name}')
            return True
        await asyncio.sleep(PARQUET_POLL_INTERVAL)
        elapsed += PARQUET_POLL_INTERVAL
    await update.message.reply_text(
        f'Portfolio parquet not found after {PARQUET_MAX_WAIT // 60} minutes '
        f'({parquet_path.name}). Stage 6 may have failed — check pipeline logs.'
    )
    return False


async def send_pdf(update, pdf_path: Path, label: str):
    if pdf_path.exists():
        with open(pdf_path, 'rb') as f:
            await update.message.reply_document(f, filename=pdf_path.name)
    else:
        await update.message.reply_text(f'{label} PDF not found ({pdf_path.name}).')


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        'NSE Pipeline Bot\n\n'
        'Commands:\n'
        '  /run_pipeline — run full pipeline (choose Rebalance or Monitor)\n'
        '  /run_regime   — run weekly regime & liquidity risk engine\n'
        '  /status       — check if a run is in progress'
    )


# ── /run_pipeline — show mode buttons ─────────────────────────────────────────

async def run_pipeline_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    keyboard = [
        [
            InlineKeyboardButton('🔄 Rebalance', callback_data='pipeline_rebalance'),
            InlineKeyboardButton('👁 Monitor',   callback_data='pipeline_monitor'),
        ]
    ]
    await update.message.reply_text(
        'Select pipeline mode:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── Button callback — mode selected ───────────────────────────────────────────

async def pipeline_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ALLOWED_USER_ID:
        return

    mode = 'rebalance' if query.data == 'pipeline_rebalance' else 'monitor'
    await query.edit_message_text(f'Mode: {mode.upper()} — starting pipeline...')
    logging.info(f'Pipeline triggered in {mode} mode by user {query.from_user.id}')

    env = os.environ.copy()
    env['STAGE6_MODE'] = mode
    env['TZ'] = 'Asia/Kolkata'

    try:
        process = await asyncio.create_subprocess_exec(
            'python3', str(PIPELINE_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE),
            env=env,
        )

        async def read_stdout():
            async for line_bytes in process.stdout:
                line = line_bytes.decode('utf-8', errors='replace').rstrip()

                if 'STARTING STAGE 1' in line:
                    await query.message.reply_text('🔄 Stage 1 — Universe fetch started')
                elif 'STARTING STAGE 2' in line:
                    await query.message.reply_text('🔄 Stage 2 — Momentum signals')
                elif 'STARTING STAGE 3' in line:
                    await query.message.reply_text('🔄 Stage 3 — Quality signals')
                elif 'STARTING STAGE 4' in line:
                    await query.message.reply_text('🔄 Stage 4 — Entry filters')
                elif 'STARTING STAGE 5' in line:
                    await query.message.reply_text('🔄 Stage 5 — Ranking & selection')
                elif 'STARTING STAGE 6' in line:
                    await query.message.reply_text('🔄 Stage 6 — Portfolio recommendations')
                elif 'STARTING INDEX FETCH' in line:
                    await query.message.reply_text('🔄 Index fetch — data/fetch_index_data.py')
                elif 'STARTING MARKET MOVEMENT — Compute' in line:
                    await query.message.reply_text('🔄 Market movement — computing metrics')
                elif 'STARTING MARKET MOVEMENT — Generate' in line:
                    await query.message.reply_text('🔄 Market movement — generating PDF')

                elif 'CHECKPOINT' in line and 'saving' in line:
                    m = re.search(r'CHECKPOINT\s+(\d+)', line)
                    if m:
                        await query.message.reply_text(
                            f'📊 Stage 1 — {m.group(1)} stocks done'
                        )

                elif 'completed successfully' in line:
                    if   'STAGE 1' in line: await query.message.reply_text('✅ Stage 1 complete')
                    elif 'STAGE 2' in line: await query.message.reply_text('✅ Stage 2 complete')
                    elif 'STAGE 3' in line: await query.message.reply_text('✅ Stage 3 complete')
                    elif 'STAGE 4' in line: await query.message.reply_text('✅ Stage 4 complete')
                    elif 'STAGE 5' in line: await query.message.reply_text('✅ Stage 5 complete')
                    elif 'STAGE 6' in line: await query.message.reply_text('✅ Stage 6 complete')
                    elif 'INDEX FETCH' in line: await query.message.reply_text('✅ Index data fetched')
                    elif 'Compute Metrics' in line: await query.message.reply_text('✅ Market metrics computed')
                    elif 'Generate PDF'    in line: await query.message.reply_text('✅ Market PDF generated')

        try:
            await asyncio.wait_for(read_stdout(), timeout=PIPELINE_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            await query.message.reply_text('Pipeline timed out after 1 hour.')
            return

        await process.wait()

        if process.returncode != 0:
            await query.message.reply_text(
                f'Pipeline failed (exit {process.returncode}). '
                f'Check logs: journalctl -u telegram-pipeline-bot -n 50'
            )
            return

        await query.message.reply_text('Pipeline complete. Generating reports...')

        if mode == 'rebalance':
            # Portfolio PDFs — rebalance only
            parquet_ready = await wait_for_parquet(query)
            if not parquet_ready:
                return

            fmt_proc = await asyncio.create_subprocess_exec(
                *FORMAT_PDF_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            fmt_stdout, fmt_stderr = await fmt_proc.communicate()

            if fmt_proc.returncode == 0:
                for pdf_path in fmt_stdout.decode('utf-8', errors='replace').strip().splitlines():
                    p = Path(pdf_path.strip())
                    if p.exists():
                        with open(p, 'rb') as f:
                            await query.message.reply_document(f, filename=p.name)
                    else:
                        await query.message.reply_text(f'PDF not found: {p.name}')
            else:
                err = fmt_stderr.decode('utf-8', errors='replace')[-500:]
                await query.message.reply_text(
                    f'Portfolio PDF failed:\n<pre>{err}</pre>', parse_mode='HTML'
                )

        # Market movement PDF — both modes
        run_date_str = pd.Timestamp.now(tz='Asia/Kolkata').strftime('%d%m%Y')
        await send_pdf(
            query, MKT_PDF_DIR / f'market_movement_report_{run_date_str}.pdf',
            'Market movement'
        )

    except Exception as e:
        await query.message.reply_text(f'Error: {e}')
        logging.exception('Pipeline error')


# ── /run_regime ───────────────────────────────────────────────────────────────

async def run_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    await update.message.reply_text('Starting regime engine...')
    logging.info(f'Regime triggered by user {update.effective_user.id}')

    env = os.environ.copy()
    env['TZ'] = 'Asia/Kolkata'

    try:
        process = await asyncio.create_subprocess_exec(
            'python3', str(REGIME_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE),
            env=env,
        )

        async def read_stdout():
            async for line_bytes in process.stdout:
                line = line_bytes.decode('utf-8', errors='replace').rstrip()

                if 'STEP 0' in line and 'Index Prices' in line:
                    await update.message.reply_text('🔄 Step 0 — checking index prices')
                elif 'STEP 1a' in line:
                    await update.message.reply_text('🔄 Step 1a — checking stock prices')
                elif 'STEP 1b' in line:
                    await update.message.reply_text('🔄 Step 1b — liquidity & risk index (4 universes)')
                elif 'STEP 2' in line and 'Narrative' in line:
                    await update.message.reply_text('🔄 Step 2 — generating narratives')
                elif 'STEP 3a' in line:
                    await update.message.reply_text('🔄 Step 3a — checking HMM index data')
                elif 'STEP 3b' in line:
                    await update.message.reply_text('🔄 Step 3b — running HMM forward algo')
                elif 'STEP 4' in line and 'Combine' in line:
                    await update.message.reply_text('🔄 Step 4 — combining outputs')
                elif 'STEP 5b' in line:
                    await update.message.reply_text('🔄 Step 5b — generating regime PDF')
                elif 'ALL DONE' in line:
                    await update.message.reply_text('✅ Regime engine complete')
                elif 'FATAL ERROR' in line:
                    await update.message.reply_text(f'❌ {line}')

        try:
            await asyncio.wait_for(read_stdout(), timeout=REGIME_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            await update.message.reply_text('Regime engine timed out after 1 hour.')
            return

        await process.wait()

        if process.returncode != 0:
            await update.message.reply_text(
                f'Regime engine failed (exit {process.returncode}). '
                f'Check logs: journalctl -u telegram-pipeline-bot -n 50'
            )
            return

        # Send regime PDF
        run_date_str = pd.Timestamp.now(tz='Asia/Kolkata').strftime('%Y-%m-%d')
        await send_pdf(
            update,
            REGIME_PDF_DIR / f'regime_report_design_{run_date_str}.pdf',
            'Regime report'
        )

    except Exception as e:
        await update.message.reply_text(f'Error: {e}')
        logging.exception('Regime error')


# ── /status ───────────────────────────────────────────────────────────────────

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    pipeline_proc = await asyncio.create_subprocess_exec(
        'pgrep', '-f', 'run_pipeline.py',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    p_out, _ = await pipeline_proc.communicate()

    regime_proc = await asyncio.create_subprocess_exec(
        'pgrep', '-f', 'regime_master.py',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    r_out, _ = await regime_proc.communicate()

    pipeline_running = bool(p_out.strip())
    regime_running   = bool(r_out.strip())

    if not pipeline_running and not regime_running:
        await update.message.reply_text('No pipeline or regime run in progress.')
    else:
        lines = []
        if pipeline_running: lines.append('🔄 Pipeline is running.')
        if regime_running:   lines.append('🔄 Regime engine is running.')
        await update.message.reply_text('\n'.join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start',        start))
    app.add_handler(CommandHandler('run_pipeline', run_pipeline_cmd))
    app.add_handler(CommandHandler('run_regime',   run_regime))
    app.add_handler(CommandHandler('status',       status))
    app.add_handler(CallbackQueryHandler(pipeline_mode_callback, pattern='^pipeline_'))

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT,  stop_event.set)

    async with app:
        await app.start()
        await app.updater.start_polling()
        logging.info('Bot started.')
        await stop_event.wait()
        await app.updater.stop()
        await app.stop()

if __name__ == '__main__':
    asyncio.run(main())
