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
active_attacks = {}  # Store attack tasks for cancellation

# ============ Flask Routes for Health Checks ============
@app.route('/')
def health():
    return jsonify({
        "status": "running", 
        "message": "Telegram Bomber Bot is active",
        "admin": ADMIN_ID,
        "bot": BOT_TOKEN.split(":")[0]
    }), 200

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200

@app.route('/stats')
def stats():
    """Admin stats endpoint"""
    return jsonify({
        "active_attacks": len(active_attacks),
        "total_users": len(user_data),
        "active_users": [uid for uid, attack in active_attacks.items() if not attack.get('stop_flag', False)]
    }), 200

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
    """Update timer message every minute"""
    job_data = context.job.data
    user_id = job_data['user_id']
    message_id = job_data['message_id']
    end_time = job_data['end_time']
    
    # Check if attack is still active
    if user_id not in active_attacks or active_attacks[user_id].get('stop_flag', False):
        return
    
    remaining = end_time - datetime.now()
    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)
    
    if remaining.total_seconds() <= 0:
        # Attack completed
        try:
            await context.bot.edit_message_text(
                text=f"✅ *Attack Completed!*\n\n"
                     f"⏰ 30 minutes of attacks finished.\n\n"
                     f"Total batches sent: {job_data.get('total_attacks', 0)}\n\n"
                     f"Use /start to start a new attack.",
                chat_id=user_id,
                message_id=message_id,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error updating timer: {e}")
        
        # Clean up
        if user_id in active_attacks:
            del active_attacks[user_id]
        return
    
    # Update timer message
    keyboard = [[InlineKeyboardButton("🛑 STOP ATTACK", callback_data=f"stop_{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.edit_message_text(
            text=f"🔥 *Attack in Progress* 🔥\n\n"
                 f"📱 Target: {job_data['numbers'][0] if len(job_data['numbers']) == 1 else f'{len(job_data["numbers"])} numbers'}\n"
                 f"⏰ Time Remaining: `{minutes:02d}:{seconds:02d}`\n"
                 f"📊 Batches Sent: `{job_data.get('total_attacks', 0)}`\n"
                 f"✅ Success Rate: `{job_data.get('success_rate', 0)}%`\n\n"
                 f"🔄 Attacks running every 5 seconds...\n"
                 f"📡 {len(bomber.apis)} APIs per batch\n"
                 f"Press STOP to cancel anytime.",
            chat_id=user_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error updating timer: {e}")

async def send_attack_batch(context: ContextTypes.DEFAULT_TYPE):
    """Send attack to numbers and update stats"""
    job_data = context.job.data
    user_id = job_data['user_id']
    numbers = job_data['numbers']
    
    # Check if attack should stop
    if job_data.get('stop_flag', False):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🛑 *Attack Stopped by User*\n\n"
                     f"Total batches sent: {job_data.get('total_attacks', 0)}\n"
                     f"Duration: {job_data.get('duration', 0)} minutes\n\n"
                     f"Use /start to start new attack.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error sending stop message: {e}")
        return
    
    # Check if time is up
    if datetime.now() >= job_data['end_time']:
        # Stop the job
        job_data['stop_flag'] = True
        return
    
    try:
        # Send attacks to all numbers simultaneously
        results = await bomber.bomb_multiple_numbers(numbers)
        
        # Update stats
        total_success = 0
        total_apis = 0
        for number, api_results in results.items():
            total_success += sum(1 for r in api_results if r["success"])
            total_apis += len(api_results)
        
        if total_apis > 0:
            success_rate = (total_success / total_apis) * 100
        else:
            success_rate = 0
        
        job_data['total_attacks'] = job_data.get('total_attacks', 0) + 1
        job_data['success_rate'] = int(success_rate)
        
        logger.info(f"Batch #{job_data['total_attacks']} sent to {len(numbers)} numbers. Success: {success_rate:.1f}%")
        
    except Exception as e:
        logger.error(f"Error sending attack batch: {e}")

# ============ Admin Functions ============
async def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel with stats"""
    user_id = update.effective_user.id
    
    if not await is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    stats_text = f"📊 *Admin Statistics*\n\n"
    stats_text += f"Active Attacks: `{len(active_attacks)}`\n"
    stats_text += f"Total Users: `{len(user_data)}`\n\n"
    
    if active_attacks:
        stats_text += "*Active Attacks:*\n"
        for uid, attack in active_attacks.items():
            if not attack.get('stop_flag', False):
                remaining = attack['end_time'] - datetime.now()
                minutes = int(remaining.total_seconds() // 60)
                stats_text += f"User `{uid}`: {minutes}m left | {attack.get('total_attacks', 0)} batches\n"
    
    keyboard = [
        [InlineKeyboardButton("🛑 Stop All Attacks", callback_data="admin_stop_all")],
        [InlineKeyboardButton("📊 Full Stats", callback_data="admin_full_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(stats_text, parse_mode="Markdown", reply_markup=reply_markup)

# ============ Telegram Bot Handlers ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show main menu"""
    user_id = update.effective_user.id
    
    # Track user
    if user_id not in user_data:
        user_data[user_id] = {"first_seen": datetime.now().isoformat()}
        logger.info(f"New user: {user_id}")
        
        # Notify admin about new user
        if user_id != ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"👤 New user started the bot!\nID: `{user_id}`\nName: {update.effective_user.first_name}",
                parse_mode="Markdown"
            )
    
    keyboard = [
        [InlineKeyboardButton("🔥 Start 30-Minute Attack", callback_data="start_attack")],
        [InlineKeyboardButton("📊 Active Attacks", callback_data="active_attacks")],
        [InlineKeyboardButton("ℹ️ Help & Info", callback_data="help")]
    ]
    
    # Add admin panel button for admin
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔥 *SMS/Call Bomber Bot* 🔥\n\n"
        "💥 *Features:*\n"
        "• ⏰ 30-minute continuous attacks\n"
        "• 🛑 Stop button to cancel anytime\n"
        "• 📱 Up to 10 numbers simultaneously\n"
        "• 🚀 9+ APIs running in parallel\n"
        "• 📊 Real-time success tracking\n\n"
        "⚠️ *WARNING*: Use responsibly. Educational purposes only.\n\n"
        f"👤 Your ID: `{user_id}`\n\n"
        "Click below to start:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "start_attack":
        user_data[user_id] = {**user_data.get(user_id, {}), "state": "awaiting_numbers"}
        await query.edit_message_text(
            "📞 *Send me the phone number(s)*\n\n"
            "Options:\n"
            "• Single number: `+919876543210`\n"
            "• Multiple numbers (up to 10):\n"
            "  `+919876543210, +919876543211`\n"
            "  or each on new line\n\n"
            "⚠️ Attack will run for 30 minutes!\n"
            "Type your number(s) now:",
            parse_mode="Markdown"
        )
    
    elif data == "active_attacks":
        if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
            attack = active_attacks[user_id]
            end_time = attack['end_time']
            remaining = end_time - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            seconds = int(remaining.total_seconds() % 60)
            
            text = f"🔄 *Active Attack*\n\n"
            text += f"📱 Target: {attack['numbers'][0] if len(attack['numbers']) == 1 else f'{len(attack["numbers"])} numbers'}\n"
            text += f"⏰ Time Left: `{minutes:02d}:{seconds:02d}`\n"
            text += f"📊 Batches Sent: `{attack.get('total_attacks', 0)}`\n"
            text += f"✅ Success Rate: `{attack.get('success_rate', 0)}%`\n\n"
            text += f"Press STOP to cancel."
            
            keyboard = [[InlineKeyboardButton("🛑 STOP ATTACK", callback_data=f"stop_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.edit_message_text(
                "❌ *No active attacks*\n\n"
                "Use /start to begin a new 30-minute attack.",
                parse_mode="Markdown"
            )
    
    elif data.startswith("stop_"):
        attacker_id = int(data.split("_")[1])
        if attacker_id == user_id or await is_admin(user_id):
            if attacker_id in active_attacks:
                active_attacks[attacker_id]['stop_flag'] = True
                await query.edit_message_text(
                    f"🛑 *Attack Stopped*\n\n"
                    f"Total batches sent: {active_attacks[attacker_id].get('total_attacks', 0)}\n"
                    f"Duration: {active_attacks[attacker_id].get('duration', 0)} minutes\n\n"
                    f"✅ Stopped successfully!\n\n"
                    f"Use /start to start new attack.",
                    parse_mode="Markdown"
                )
                # Notify admin if admin stopped someone else's attack
                if attacker_id != user_id:
                    await context.bot.send_message(
                        chat_id=attacker_id,
                        text=f"🛑 Your attack was stopped by admin.\nTotal batches: {active_attacks[attacker_id].get('total_attacks', 0)}",
                        parse_mode="Markdown"
                    )
            else:
                await query.edit_message_text(
                    "❌ No active attack found.",
                    parse_mode="Markdown"
                )
        else:
            await query.answer("You can only stop your own attacks!", show_alert=True)
    
    elif data == "admin_panel":
        if await is_admin(user_id):
            await admin_panel(update, context)
        else:
            await query.answer("Unauthorized!", show_alert=True)
    
    elif data == "admin_stop_all":
        if await is_admin(user_id):
            stopped = 0
            for uid, attack in list(active_attacks.items()):
                if not attack.get('stop_flag', False):
                    attack['stop_flag'] = True
                    stopped += 1
                    await context.bot.send_message(
                        chat_id=uid,
                        text="🛑 Admin has stopped your attack.",
                        parse_mode="Markdown"
                    )
            await query.edit_message_text(
                f"✅ Stopped {stopped} active attacks.",
                parse_mode="Markdown"
            )
    
    elif data == "admin_full_stats":
        if await is_admin(user_id):
            stats = f"📊 *Full Statistics*\n\n"
            stats += f"Active Attacks: {len(active_attacks)}\n"
            stats += f"Total Users: {len(user_data)}\n\n"
            stats += f"*API Configuration:*\n"
            for i, api in enumerate(bomber.apis, 1):
                stats += f"{i}. {api['name']}\n"
            await query.edit_message_text(stats, parse_mode="Markdown")
    
    elif data == "help":
        await query.edit_message_text(
            "ℹ️ *How to Use*\n\n"
            "1️⃣ Click 'Start 30-Minute Attack'\n"
            "2️⃣ Enter phone number(s) with country code\n"
            "3️⃣ Bot will attack for 30 minutes\n"
            "4️⃣ Attacks run every 5 seconds\n"
            "5️⃣ Press STOP button to cancel anytime\n\n"
            "*Commands:*\n"
            "/start - Main menu\n"
            "/stop - Stop current attack\n"
            "/status - Check attack status\n\n"
            "*Number Format:*\n"
            "• Indian: `9876543210` or `+919876543210`\n"
            "• International: `+[country code][number]`\n\n"
            "*⚠️ Disclaimer:*\n"
            "Educational purposes only. Use responsibly.",
            parse_mode="Markdown"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id not in user_data or "state" not in user_data[user_id]:
        await update.message.reply_text("Please use /start to begin.")
        return
    
    state = user_data[user_id]["state"]
    
    if state == "awaiting_numbers":
        await start_30_minute_attack(update, text)
        user_data[user_id] = user_data.get(user_id, {})

async def start_30_minute_attack(update: Update, numbers_text: str):
    """Start a 30-minute continuous attack"""
    user_id = update.effective_user.id
    
    # Parse numbers
    if '\n' in numbers_text:
        numbers = [clean_number(n.strip()) for n in numbers_text.split('\n') if n.strip()]
    else:
        numbers = [clean_number(n.strip()) for n in numbers_text.split(',') if n.strip()]
    
    # Validate numbers
    numbers = [n for n in numbers if is_valid_number(n)]
    
    if not numbers:
        await update.message.reply_text(
            "❌ *Invalid phone number(s)!*\n\n"
            "Use format: `+919876543210`\n\n"
            "Example: 9876543210 or +919876543210\n\n"
            "Try again with /start",
            parse_mode="Markdown"
        )
        return
    
    if len(numbers) > 10:
        await update.message.reply_text(
            f"❌ *Too many numbers!*\n\n"
            f"You sent {len(numbers)} numbers. Maximum is 10.\n\n"
            f"Please try again.",
            parse_mode="Markdown"
        )
        return
    
    # Check if user already has active attack
    if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
        await update.message.reply_text(
            "⚠️ *You already have an active attack!*\n\n"
            "Please stop the current attack first using STOP button or /stop command.",
            parse_mode="Markdown"
        )
        return
    
    # Send confirmation message with timer
    end_time = datetime.now() + timedelta(minutes=30)
    
    keyboard = [[InlineKeyboardButton("🛑 STOP ATTACK", callback_data=f"stop_{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        f"🔥 *Attack Started!* 🔥\n\n"
        f"📱 Target(s): {', '.join(numbers[:3])}{'...' if len(numbers) > 3 else ''}\n"
        f"⏰ Duration: `30 minutes`\n"
        f"🔄 Frequency: Every 5 seconds\n"
        f"📊 Total batches: ~360 attacks\n"
        f"🚀 APIs per batch: {len(bomber.apis)}\n\n"
        f"✅ Attack will run for 30 minutes.\n"
        f"Press STOP button to cancel anytime.\n\n"
        f"⏱️ *Timer starting...*",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    # Store attack data
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
    
    # Set up job queue for attacks
    job_queue = context.application.job_queue
    
    # Schedule attack batches (every 5 seconds)
    attack_job = job_queue.run_repeating(
        send_attack_batch,
        interval=5,
        first=1,
        data=attack_data,
        name=f"attack_{user_id}"
    )
    
    # Schedule timer updates (every 30 seconds)
    timer_job = job_queue.run_repeating(
        update_timer_message,
        interval=30,
        first=1,
        data={
            'user_id': user_id,
            'message_id': message.message_id,
            'end_time': end_time,
            'numbers': numbers,
            'total_attacks': 0,
            'success_rate': 0
        },
        name=f"timer_{user_id}"
    )
    
    # Store jobs for cleanup
    attack_data['attack_job'] = attack_job
    attack_data['timer_job'] = timer_job
    
    # Notify admin about new attack
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🔥 New attack started!\nUser: `{user_id}`\nTargets: {len(numbers)} number(s)\nDuration: 30 minutes",
        parse_mode="Markdown"
    )

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop current attack"""
    user_id = update.effective_user.id
    
    if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
        active_attacks[user_id]['stop_flag'] = True
        await update.message.reply_text(
            f"🛑 *Attack Stopped*\n\n"
            f"Total batches sent: {active_attacks[user_id].get('total_attacks', 0)}\n"
            f"Duration: {active_attacks[user_id].get('duration', 0)} minutes\n\n"
            f"Use /start to begin new attack.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ No active attack found.\n\n"
            "Use /start to begin a new attack.",
            parse_mode="Markdown"
        )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check attack status"""
    user_id = update.effective_user.id
    
    if user_id in active_attacks and not active_attacks[user_id].get('stop_flag', False):
        attack = active_attacks[user_id]
        end_time = attack['end_time']
        remaining = end_time - datetime.now()
        minutes = int(remaining.total_seconds() // 60)
        seconds = int(remaining.total_seconds() % 60)
        
        if remaining.total_seconds() <= 0:
            await update.message.reply_text("✅ Attack completed!")
            return
        
        text = f"🔄 *Attack Status*\n\n"
        text += f"📱 Target: {attack['numbers'][0] if len(attack['numbers']) == 1 else f'{len(attack["numbers"])} numbers'}\n"
        text += f"⏰ Time Left: `{minutes:02d}:{seconds:02d}`\n"
        text += f"📊 Batches Sent: `{attack.get('total_attacks', 0)}`\n"
        text += f"✅ Success Rate: `{attack.get('success_rate', 0)}%`\n\n"
        text += f"Each batch sends to {len(attack['numbers'])} number(s) × {len(bomber.apis)} APIs = {len(attack['numbers']) * len(bomber.apis)} requests"
        
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "❌ No active attack.\n\n"
            "Use /start to begin a 30-minute attack.",
            parse_mode="Markdown"
        )

# ============ Main Bot Runner ============
def run_bot():
    """Run the bot with polling"""
    if not BOT_TOKEN:
        logger.error("Cannot start bot - TELEGRAM_TOKEN not set")
        return
    
    # Create application
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("stop", stop_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("admin", admin_panel))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start polling
    logger.info(f"Starting bot with token: {BOT_TOKEN.split(':')[0]}...")
    logger.info(f"Admin ID: {ADMIN_ID}")
    telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)

# ============ Flask App with Background Bot ============
if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Run Flask app
    logger.info(f"Starting Flask app on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)