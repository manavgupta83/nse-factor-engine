"""
Telegram bot — NSE pipeline trigger
Whitelists a single Telegram user ID. All other senders are silently ignored.
"""

import asyncio, signal, logging, os, re
from pathlib import Path
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ['TELEGRAM_BOT_TOKEN']
ALLOWED_USER_ID  = int(os.environ['TELEGRAM_USER_ID'])
PIPELINE_CMD     = ['python3', '/home/ec2-user/nse-factor-engine/run_pipeline.py']
FORMAT_PDF_CMD   = ['python3', '/home/ec2-user/nse-factor-engine/ops/format_portfolio_pdf.py']
PDF_DIR          = Path('/home/ec2-user/nse-factor-engine/market_movement/data')
SIGNALS_DIR      = Path('/home/ec2-user/nse-factor-engine/signals/stage6')
PIPELINE_TIMEOUT = 3600
PARQUET_POLL_INTERVAL = 5    # seconds between checks
PARQUET_MAX_WAIT      = 300  # 5 minutes max
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s',
    level=logging.INFO,
    filename='/home/ec2-user/telegram_bot.log'
)

def is_allowed(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID

async def wait_for_parquet(update):
    """
    Polls for today's portfolio_recommendations parquet every 5s.
    Returns True if found within 5 minutes, False otherwise.
    """
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        'NSE Pipeline Bot ready.\n'
        'Commands:\n'
        '  /run_pipeline — run the full backtest pipeline\n'
        '  /status       — check if a run is in progress'
    )

async def run_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    await update.message.reply_text('Starting pipeline...')
    logging.info(f'Pipeline triggered by user {update.effective_user.id}')

    try:
        process = await asyncio.create_subprocess_exec(
            *PIPELINE_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        async def read_stdout():
            async for line_bytes in process.stdout:
                line = line_bytes.decode('utf-8', errors='replace').rstrip()

                # ── Stage starts ──────────────────────────────────────────────
                if 'STARTING STAGE 1' in line:
                    await update.message.reply_text('🔄 Stage 1 — Universe data fetch started')
                elif 'STARTING STAGE 2' in line:
                    await update.message.reply_text('🔄 Stage 2 — Momentum signals computing')
                elif 'STARTING STAGE 3' in line:
                    await update.message.reply_text('🔄 Stage 3 — Quality signals computing')
                elif 'STARTING STAGE 4' in line:
                    await update.message.reply_text('🔄 Stage 4 — Entry filters computing')
                elif 'STARTING STAGE 5' in line:
                    await update.message.reply_text('🔄 Stage 5 — Ranking & selection')
                elif 'STARTING STAGE 6' in line:
                    await update.message.reply_text('🔄 Stage 6 — Portfolio recommendations')
                elif 'STARTING MARKET MOVEMENT — Fetch' in line:
                    await update.message.reply_text('🔄 Market movement — fetching index data')
                elif 'STARTING MARKET MOVEMENT — Compute' in line:
                    await update.message.reply_text('🔄 Market movement — computing metrics')
                elif 'STARTING MARKET MOVEMENT — Generate' in line:
                    await update.message.reply_text('🔄 Market movement — generating PDF')

                # ── Stage 1 checkpoints ───────────────────────────────────────
                elif 'CHECKPOINT' in line and 'saving' in line:
                    m = re.search(r'CHECKPOINT\s+(\d+)', line)
                    if m:
                        await update.message.reply_text(
                            f'📊 Stage 1 running — {m.group(1)} stocks done'
                        )

                # ── Stage completions ─────────────────────────────────────────
                elif 'completed successfully' in line:
                    if 'STAGE 1' in line:
                        await update.message.reply_text('✅ Stage 1 complete')
                    elif 'STAGE 2' in line:
                        await update.message.reply_text('✅ Stage 2 complete')
                    elif 'STAGE 3' in line:
                        await update.message.reply_text('✅ Stage 3 complete')
                    elif 'STAGE 4' in line:
                        await update.message.reply_text('✅ Stage 4 complete')
                    elif 'STAGE 5' in line:
                        await update.message.reply_text('✅ Stage 5 complete')
                    elif 'STAGE 6' in line:
                        await update.message.reply_text('✅ Stage 6 complete')
                    elif 'Fetch Index' in line:
                        await update.message.reply_text('✅ Market data fetched')
                    elif 'Compute Metrics' in line:
                        await update.message.reply_text('✅ Market metrics computed')
                    elif 'Generate PDF' in line:
                        await update.message.reply_text('✅ PDF generated')

        try:
            await asyncio.wait_for(read_stdout(), timeout=PIPELINE_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            await update.message.reply_text('Pipeline timed out after 1 hour.')
            return

        await process.wait()

        if process.returncode != 0:
            await update.message.reply_text(
                f'Pipeline failed (exit {process.returncode}). '
                f'Check logs: journalctl -u telegram-pipeline-bot -n 50'
            )
            return

        await update.message.reply_text('Pipeline completed. Generating reports...')

        # ── Wait for parquet to be available ──────────────────────────────────
        parquet_ready = await wait_for_parquet(update)
        if not parquet_ready:
            return

        # ── Generate portfolio PDFs ───────────────────────────────────────────
        fmt_proc = await asyncio.create_subprocess_exec(
            *FORMAT_PDF_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        fmt_stdout, fmt_stderr = await fmt_proc.communicate()

        if fmt_proc.returncode == 0:
            pdf_paths = fmt_stdout.decode('utf-8', errors='replace').strip().splitlines()
            for pdf_path in pdf_paths:
                p = Path(pdf_path.strip())
                if p.exists():
                    with open(p, 'rb') as f:
                        await update.message.reply_document(f, filename=p.name)
                else:
                    await update.message.reply_text(f'PDF not found: {p.name}')
        else:
            err = fmt_stderr.decode('utf-8', errors='replace')[-500:]
            await update.message.reply_text(
                f'Portfolio PDF generation failed:\n<pre>{err}</pre>',
                parse_mode='HTML'
            )

        # ── Send market movement PDF ──────────────────────────────────────────
        run_date_str = pd.Timestamp.now(tz='Asia/Kolkata').strftime('%d%m%Y')
        mkt_pdf      = PDF_DIR / f'market_movement_report_{run_date_str}.pdf'

        if mkt_pdf.exists():
            with open(mkt_pdf, 'rb') as f:
                await update.message.reply_document(f, filename=mkt_pdf.name)
        else:
            await update.message.reply_text(
                f'Market movement PDF not found ({mkt_pdf.name}). '
                f'Report stage may have failed — check pipeline logs.'
            )

    except Exception as e:
        await update.message.reply_text(f'Error: {e}')
        logging.exception('Pipeline error')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    proc = await asyncio.create_subprocess_exec(
        'pgrep', '-f', 'run_pipeline.py',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )
    stdout, _ = await proc.communicate()
    if stdout.strip():
        await update.message.reply_text('Pipeline is currently running.')
    else:
        await update.message.reply_text('No pipeline run in progress.')

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start',        start))
    app.add_handler(CommandHandler('run_pipeline', run_pipeline))
    app.add_handler(CommandHandler('status',       status))

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
