import asyncio
import os
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from api_bomber import APIBomber

# ============ CONFIG ============
BOT_TOKEN = "8758206225:AAFihR79UEdrGEJKdaheI-EpSoTQ7n9q7Tw"
ADMIN_ID = 1029883095
PORT = int(os.environ.get("PORT", 5000))

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Bot setup
bomber = APIBomber(max_concurrent=10)
user_data = {}
active_attacks = {}

# ============ FLASK ROUTES ============
@app.route('/')
@app.route('/health')
def health():
    return jsonify({"status": "running", "message": "Bot is active", "timestamp": datetime.now().isoformat()})

# ============ HELPER FUNCTIONS ============
def clean_number(number):
    number = number.strip()
    if not number.startswith('+'):
        if len(number) == 10 and number.isdigit():
            number = '+91' + number
        else:
            number = '+' + number
    return number

def is_valid_number(number):
    return number.startswith('+') and number[1:].isdigit() and 10 <= len(number[1:]) <= 15

# ============ TELEGRAM HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔥 Start 30-Min Attack", callback_data="attack")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    await update.message.reply_text(
        "🔥 *Bomber Bot Active* 🔥\n\nClick below to start 30-minute attack:\n\n⚠️ Educational purpose only",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "attack":
        await query.edit_message_text("📞 Send phone number:\n\nExample: `9876543210` or `+919876543210`\n\nMax 10 numbers (separate with comma):\n`9876543210, 9876543211`", parse_mode="Markdown")
        user_data[query.from_user.id] = {"state": "number"}
    
    elif query.data == "status":
        user_id = query.from_user.id
        if user_id in active_attacks and not active_attacks[user_id].get("stop"):
            attack = active_attacks[user_id]
            remaining = attack["end_time"] - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            await query.edit_message_text(f"🔥 *Active Attack*\n\nBatches: {attack.get('count', 0)}\nTime Left: {minutes} minutes\nPress /stop to cancel", parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ No active attack. Start new with /start", parse_mode="Markdown")
    
    elif query.data == "help":
        await query.edit_message_text(
            "📞 *How to Use*\n\n1. Click 'Start Attack'\n2. Send phone number\n3. Attack runs 30 minutes\n4. Use /stop to cancel\n\n*Commands:*\n/start - Main menu\n/stop - Stop attack\n/status - Check status\n\n*Number Format:*\nIndian: 9876543210\nInternational: +919876543210", 
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id not in user_data or user_data[user_id].get("state") != "number":
        await update.message.reply_text("Use /start first")
        return
    
    # Parse numbers (support comma separated)
    if ',' in text:
        numbers_raw = [n.strip() for n in text.split(',')]
    else:
        numbers_raw = [text]
    
    # Clean and validate numbers
    numbers = []
    for num in numbers_raw:
        cleaned = clean_number(num)
        if is_valid_number(cleaned):
            numbers.append(cleaned)
    
    if not numbers:
        await update.message.reply_text("❌ Invalid number! Use: 9876543210 or +919876543210")
        return
    
    if len(numbers) > 10:
        await update.message.reply_text(f"❌ Too many numbers! Max 10. You sent {len(numbers)}")
        return
    
    # Check if already have active attack
    if user_id in active_attacks and not active_attacks[user_id].get("stop"):
        await update.message.reply_text("⚠️ You already have an active attack! Use /stop first")
        return
    
    # Start attack
    end_time = datetime.now() + timedelta(minutes=30)
    active_attacks[user_id] = {
        "numbers": numbers,
        "end_time": end_time,
        "count": 0,
        "stop": False,
        "success_rate": 0
    }
    
    # Schedule attack job
    job_queue = context.application.job_queue
    job_queue.run_repeating(
        lambda ctx: send_attack(ctx, user_id),
        interval=5,
        first=1,
        data={"user_id": user_id}
    )
    
    # Schedule timer update job
    job_queue.run_repeating(
        lambda ctx: update_status(ctx, user_id),
        interval=30,
        first=30,
        data={"user_id": user_id}
    )
    
    numbers_text = ', '.join(numbers[:3])
    if len(numbers) > 3:
        numbers_text += f' and {len(numbers)-3} more'
    
    await update.message.reply_text(
        f"✅ *Attack Started!*\n\n📱 Target: {numbers_text}\n⏰ Duration: 30 minutes\n🔄 Frequency: Every 5 seconds\n\nUse /stop to cancel anytime",
        parse_mode="Markdown"
    )
    user_data[user_id] = {}

async def send_attack(context: ContextTypes.DEFAULT_TYPE, user_id):
    """Send attack batch"""
    if user_id not in active_attacks:
        return
    
    attack = active_attacks[user_id]
    if attack.get("stop") or datetime.now() >= attack["end_time"]:
        if user_id in active_attacks:
            # Send completion message
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ *Attack Completed!*\n\nTotal batches: {attack.get('count', 0)}\nDuration: 30 minutes\n\nUse /start for new attack",
                    parse_mode="Markdown"
                )
            except:
                pass
            del active_attacks[user_id]
        return
    
    try:
        results = await bomber.bomb_multiple_numbers(attack["numbers"])
        
        # Calculate success rate
        total_success = 0
        total_apis = 0
        for number, api_results in results.items():
            total_success += sum(1 for r in api_results if r["success"])
            total_apis += len(api_results)
        
        success_rate = (total_success / total_apis * 100) if total_apis > 0 else 0
        attack["count"] = attack.get("count", 0) + 1
        attack["success_rate"] = int(success_rate)
        
        logger.info(f"User {user_id}: Batch #{attack['count']} - Success: {success_rate:.1f}%")
        
    except Exception as e:
        logger.error(f"Attack error for {user_id}: {e}")

async def update_status(context: ContextTypes.DEFAULT_TYPE, user_id):
    """Update status message"""
    if user_id not in active_attacks:
        return
    
    attack = active_attacks[user_id]
    if attack.get("stop"):
        return
    
    remaining = attack["end_time"] - datetime.now()
    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)
    
    if remaining.total_seconds() <= 0:
        return
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📊 *Attack Status*\n\nBatches Sent: {attack.get('count', 0)}\nSuccess Rate: {attack.get('success_rate', 0)}%\nTime Remaining: {minutes:02d}:{seconds:02d}",
            parse_mode="Markdown"
        )
    except:
        pass

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop attack command"""
    user_id = update.effective_user.id
    if user_id in active_attacks:
        attack = active_attacks[user_id]
        attack["stop"] = True
        await update.message.reply_text(
            f"🛑 *Attack Stopped*\n\nTotal batches sent: {attack.get('count', 0)}\nSuccess Rate: {attack.get('success_rate', 0)}%\n\nUse /start for new attack",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ No active attack", parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check status command"""
    user_id = update.effective_user.id
    if user_id in active_attacks and not active_attacks[user_id].get("stop"):
        attack = active_attacks[user_id]
        remaining = attack["end_time"] - datetime.now()
        minutes = int(remaining.total_seconds() // 60)
        seconds = int(remaining.total_seconds() % 60)
        
        await update.message.reply_text(
            f"📊 *Attack Status*\n\nBatches: {attack.get('count', 0)}\nSuccess Rate: {attack.get('success_rate', 0)}%\nTime Left: {minutes:02d}:{seconds:02d}\n\nUse /stop to cancel",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ No active attack", parse_mode="Markdown")

# ============ MAIN ============
def run_bot():
    """Run bot in background thread"""
    try:
        logger.info("Creating bot application...")
        bot_app = Application.builder().token(BOT_TOKEN).build()
        
        logger.info("Adding handlers...")
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("stop", stop_command))
        bot_app.add_handler(CommandHandler("status", status_command))
        bot_app.add_handler(CallbackQueryHandler(button_handler))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("Starting bot polling...")
        bot_app.run_polling()
        
    except Exception as e:
        logger.error(f"Bot error: {e}")

# ============ START APPLICATION ============
if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait a bit for bot to initialize
    import time
    time.sleep(2)
    
    # Start Flask server
    logger.info(f"Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
