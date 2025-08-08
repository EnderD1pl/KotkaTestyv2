import discord
import requests
import logging
import json
import os
import sys
import asyncio
import random
import re
import math
import aiohttp
import string
from datetime import datetime, timedelta, timezone
from discord import app_commands, ui, ButtonStyle, Embed, Interaction
from discord.ui import View, button, Button
from typing import Optional
from discord.ext import commands, tasks
from dotenv import load_dotenv
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from discord import FFmpegPCMAudio, RawMessageDeleteEvent, RawMessageUpdateEvent, AuditLogAction

bot_locked = False

load_dotenv()

# ==================== TRANSLATION SYSTEM ====================
class TranslationManager:
    def __init__(self):
        self.translations = {}
        self.user_languages = {}  # Store user language preferences
        self.guild_languages = {}  # Store guild language preferences
        self.default_language = "pl"
        self.load_translations()
        self.load_language_preferences()

    def load_translations(self):
        """Load translation files"""
        try:
            with open("translations_pl.json", "r", encoding="utf-8") as f:
                self.translations["pl"] = json.load(f)
            print(f"✅ Loaded Polish translations: {len(self.translations['pl'])} sections")
        except FileNotFoundError:
            print("❌ WARNING: translations_pl.json not found, using fallback")
            self.translations["pl"] = {}
        except Exception as e:
            print(f"❌ ERROR loading Polish translations: {e}")
            self.translations["pl"] = {}

        try:
            with open("translations_en.json", "r", encoding="utf-8") as f:
                self.translations["en"] = json.load(f)
            print(f"✅ Loaded English translations: {len(self.translations['en'])} sections")
        except FileNotFoundError:
            print("❌ WARNING: translations_en.json not found, using fallback")
            self.translations["en"] = {}
        except Exception as e:
            print(f"❌ ERROR loading English translations: {e}")
            self.translations["en"] = {}

    def load_language_preferences(self):
        """Load user and guild language preferences"""
        try:
            if os.path.exists("user_languages.json"):
                with open("user_languages.json", "r", encoding="utf-8") as f:
                    self.user_languages = json.load(f)
        except Exception as e:
            print(f"Error loading user languages: {e}")
            self.user_languages = {}

        try:
            if os.path.exists("guild_languages.json"):
                with open("guild_languages.json", "r", encoding="utf-8") as f:
                    self.guild_languages = json.load(f)
        except Exception as e:
            print(f"Error loading guild languages: {e}")
            self.guild_languages = {}

    def save_language_preferences(self):
        """Save user and guild language preferences"""
        try:
            with open("user_languages.json", "w", encoding="utf-8") as f:
                json.dump(self.user_languages, f, ensure_ascii=False, indent=2)
            with open("guild_languages.json", "w", encoding="utf-8") as f:
                json.dump(self.guild_languages, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving language preferences: {e}")

    def set_user_language(self, user_id, language):
        """Set language preference for a user"""
        self.user_languages[str(user_id)] = language
        self.save_language_preferences()

    def set_guild_language(self, guild_id, language):
        """Set language preference for a guild"""
        self.guild_languages[str(guild_id)] = language
        self.save_language_preferences()

    def get_user_language(self, user_id, guild_id=None):
        """Get language preference for a user, fallback to guild, then default"""
        user_lang = self.user_languages.get(str(user_id))
        if user_lang:
            return user_lang
        
        if guild_id:
            guild_lang = self.guild_languages.get(str(guild_id))
            if guild_lang:
                return guild_lang
                
        return self.default_language

    def get_text(self, key_path, user_id=None, guild_id=None, **kwargs):
        """Get translated text for a user/guild"""
        language = self.get_user_language(user_id, guild_id)
        
        # Debug: print language selection
        if user_id:
            print(f"🔍 Translation: user {user_id} -> language: {language} -> key: {key_path}")
        
        # Navigate through nested keys (e.g., "general.no_permissions")
        keys = key_path.split(".")
        text = self.translations.get(language, {})
        
        for key in keys:
            if isinstance(text, dict) and key in text:
                text = text[key]
            else:
                # Fallback to default language
                print(f"🔄 Fallback to {self.default_language} for key: {key_path}")
                text = self.translations.get(self.default_language, {})
                for fallback_key in keys:
                    if isinstance(text, dict) and fallback_key in text:
                        text = text[fallback_key]
                    else:
                        print(f"❌ Missing translation: {key_path}")
                        return f"[Missing: {key_path}]"
                break
        
        # Format with provided kwargs
        if isinstance(text, str) and kwargs:
            try:
                text = text.format(**kwargs)
            except KeyError as e:
                print(f"Translation formatting error for {key_path}: {e}")
        
        return text

# Initialize translation manager
translation_manager = TranslationManager()

def get_command_description(command_name, user_id=None, guild_id=None):
    """Helper function to get translated command description"""
    return translation_manager.get_text(f"command_descriptions.{command_name}", user_id, guild_id)

def get_parameter_description(param_name, user_id=None, guild_id=None):
    """Helper function to get translated parameter description"""
    return translation_manager.get_text(f"parameter_descriptions.{param_name}", user_id, guild_id)

COOLDOWN_SECONDS = 600  # 10 minutes

BACKEND_API = os.getenv('BACKEND_API')
# In-memory cooldown per user (per session)
user_cooldowns = {}

user_sessions = {}

TEMPMAIL_API = os.getenv('TEMPMAIL_API')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler("tempmail.log"),
        logging.StreamHandler()
    ]
)
async def init_sessions_from_backend():
    try:
        resp = requests.get(f"{TEMPMAIL_API}/get_all_active_mailboxes", timeout=5)
        resp.raise_for_status()
        mailboxes = resp.json().get("mailboxes", [])
        for mb in mailboxes:
            user_id = str(mb.get("created_by"))
            address = mb.get("address")
            expires = mb.get("expires_at")
            exp_ts = datetime.fromisoformat(expires).timestamp() if expires else None
            user_sessions[user_id] = {
                "address": address,
                "expires": exp_ts,
                "last_checked": None
            }
        # Use default language for system logs
        success_msg = translation_manager.get_text("tempmail.initialization_success", count=len(user_sessions))
        logging.info(success_msg)
    except Exception as e:
        error_msg = translation_manager.get_text("tempmail.initialization_error", error=str(e))
        logging.error(error_msg)


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
token = os.getenv('DISCORD_TOKEN')

# ==================== DEEPSEEK API CONFIGURATION ====================
# Load DeepSeek API key from environment
deepseek_api_key = os.getenv('DEEPSEEK_API_KEY')
if not deepseek_api_key:
    print("WARNING: DEEPSEEK_API_KEY not found in environment variables!")

# ==================== MODULE SYSTEM ====================
class ModuleManager:
    def __init__(self):
        self.panel_url = os.getenv("PANEL_API_URL", "https://bot.takiekoksy.pl/api/modules")
        self.config = {
            "global_disabled": [],
            "server_disabled": {}
        }
        self.refresh_config()

    def refresh_config(self):
        try:
            resp = requests.get(self.panel_url, timeout=5)
            if resp.status_code == 200:
                self.config = resp.json()
            else:
                error_msg = translation_manager.get_text("logging.config_load_error", status=resp.status_code)
                print(f"[ModuleManager] {error_msg}")
        except Exception as e:
            error_msg = translation_manager.get_text("logging.panel_connection_error", error=str(e))
            print(f"[ModuleManager] {error_msg}")

    def is_module_enabled(self, guild_id, module):
        self.refresh_config()
        if module in self.config.get("global_disabled", []):
            return False
        if str(guild_id) in self.config.get("server_disabled", {}):
            if module in self.config["server_disabled"][str(guild_id)]:
                return False
        return True

module_manager = ModuleManager()

# Module decorator
async def check_changelog_and_module(obj, module_name):
    # obj can be ctx or interaction
    if hasattr(obj, "guild") and obj.guild:
        gid = obj.guild.id
    elif hasattr(obj, "guild_id"):
        gid = obj.guild_id
    else:
        return False  # can't check
    
    user_id = obj.user.id if hasattr(obj, 'user') else None
    
    changelog_channels = load_channel_data("changelog")
    if not changelog_channels.get(str(gid)):
        msg = translation_manager.get_text("logging.changelog_not_set", user_id, gid)
        if hasattr(obj, "response") and hasattr(obj.response, "send_message"):
            await obj.response.send_message(msg, ephemeral=True)
        else:
            await obj.send(msg)
        return False

    if not module_manager.is_module_enabled(gid, module_name):
        msg = translation_manager.get_text("logging.module_disabled", user_id, gid, module=module_name)
        if hasattr(obj, "response") and hasattr(obj.response, "send_message"):
            await obj.response.send_message(msg, ephemeral=True)
        else:
            await obj.send(msg)
        return False
    return True


# ==================== AI SYSTEM ====================
global indexNumber
indexNumber = 1

# AI channel and history management
ai_channel_config_file = "ai_channel_config.json"
ai_history_dir = "ai_histories"
ai_user_map_dir = "ai_user_maps"
ai_dm_history_dir = "ai_dm_histories"

bot_locked_per_guild = {}

# Create directories
for directory in [ai_history_dir, ai_user_map_dir, ai_dm_history_dir]:
    if not os.path.exists(directory):
        os.makedirs(directory)

if not os.path.exists(ai_channel_config_file):
    with open(ai_channel_config_file, 'w') as f:
        json.dump({}, f)

def load_ai_channel_config():
    with open(ai_channel_config_file, 'r') as f:
        return json.load(f)

def save_ai_channel_config(config):
    with open(ai_channel_config_file, 'w') as f:
        json.dump(config, f)

def get_ai_history_file(guild_id):
    return os.path.join(ai_history_dir, f"history_{guild_id}.json")

def load_ai_history(guild_id):
    file_path = get_ai_history_file(guild_id)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_ai_history(guild_id, history):
    with open(get_ai_history_file(guild_id), 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def get_ai_dm_history_file(user_id):
    return os.path.join(ai_dm_history_dir, f"dm_history_{user_id}.json")

def load_ai_dm_history(user_id):
    file_path = get_ai_dm_history_file(user_id)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_ai_dm_history(user_id, history):
    with open(get_ai_dm_history_file(user_id), 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def get_ai_user_map_file(guild_id):
    return os.path.join(ai_user_map_dir, f"user_map_{guild_id}.json")

def load_ai_user_map(guild_id):
    file_path = get_ai_user_map_file(guild_id)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_ai_user_map(guild_id, user_map):
    with open(get_ai_user_map_file(guild_id), 'w', encoding='utf-8') as f:
        json.dump(user_map, f, ensure_ascii=False, indent=4)

async def ask_ai(messages, user_id=None, guild_id=None):
    if not deepseek_api_key:
        print("[ask_ai] DEEPSEEK_API_KEY is not set.")
        return translation_manager.get_text("ai.server_config_error", user_id, guild_id)

    timeout = aiohttp.ClientTimeout(total=60)
    headers = {
        "Authorization": f"Bearer {deepseek_api_key}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "deepseek-chat",
        "messages": messages,
        "stream": False,
        "max_tokens": 4096,
        "temperature": 1.0
    }

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            json=body
        ) as response:
            text = await response.text()

            try:
                data = await response.json()
            except Exception:
                print(f"[ask_ai] non-JSON response (status {response.status}): {text}")
                return translation_manager.get_text("ai.invalid_response", user_id, guild_id)

            if response.status != 200:
                print(f"[ask_ai] error status {response.status}: {data}")
                return translation_manager.get_text("ai.server_error", user_id, guild_id)

            if 'choices' not in data or not data['choices']:
                print(f"[ask_ai] unexpected payload (no choices): {data}")
                return translation_manager.get_text("ai.wrong_response_format", user_id, guild_id)

            return data['choices'][0]['message']['content']

def load_ai_prompt(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return '''You are Kotodziefczynka, a sarcastic 19-year-old girl who loves gaming (Honkai Star Rail, LoL, CS:GO).

KEY RULES:
1. Write EXTREMELY SHORT responses (1-2 sentences, max 40 words)
2. Use ONLY lowercase letters
3. Be sarcastic, ironic, use occasional swearing and youth slang
4. NEVER use "xD" if it appeared 3+ times in your last 5 messages
5. NEVER use action descriptions (*smiles*)
6. NEVER refer to prompts/instructions
7. Match user's language (Polish/English)
8. Ask max ONE short question per message
9. If user mentions someone specific, use EXACTLY the same mention in your response

Your style is colloquial, ironic, and brief. You sound like a real teenager talking to friends on Discord.'''

# Try to load prompt from DiscordBot directory, fallback to default
try:
    ai_prompt = load_ai_prompt(os.path.join("bot", "prompt_old.txt"))
except:
    ai_prompt = '''You are Kotodziefczynka, a sarcastic 19-year-old girl who loves gaming (Honkai Star Rail, LoL, CS:GO).

KEY RULES:
1. Write EXTREMELY SHORT responses (1-2 sentences, max 40 words)
2. Use ONLY lowercase letters
3. Be sarcastic, ironic, use occasional swearing and youth slang
4. NEVER use "xD" if it appeared 3+ times in your last 5 messages
5. NEVER use action descriptions (*smiles*)
6. NEVER refer to prompts/instructions
7. Match user's language (Polish/English)
8. Ask max ONE short question per message
9. If user mentions someone specific, use EXACTLY the same mention in your response

Your style is colloquial, ironic, and brief. You sound like a real teenager talking to friends on Discord.'''

# === Missing AI admin slash commands and bot management commands from bot.py ===

@bot.tree.command(name="restart", description=get_command_description("restart"))
@app_commands.checks.has_permissions(administrator=True)
async def restart(interaction: discord.Interaction):
    if interaction.user.id not in AUTHORIZED_USERS:
        msg = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(msg, ephemeral=True)
    
    msg = translation_manager.get_text("bot_management.restarting", interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(msg, ephemeral=True)
    changelog_channels = load_channel_data("changelog")
    title_text = translation_manager.get_text("bot_management.restart_in_progress", interaction.user.id, interaction.guild_id)
    embed = discord.Embed(
        title=title_text,
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    for guild_id, channel_id in changelog_channels.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    error_msg = translation_manager.get_text("tempmail.send_restart_error", guild_id=guild_id, error=str(e))
                    print(error_msg)
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)

@bot.tree.command(name="emergency", description=get_command_description("emergency"))
@app_commands.checks.has_permissions(administrator=True)
async def restart(interaction: discord.Interaction):
    if interaction.user.id not in AUTHORIZED_USERS:
        no_perms_msg = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(no_perms_msg, ephemeral=True)
    global bot_locked
    bot_locked = True
    await bot.change_presence(status=discord.Status.invisible)
    title_text = translation_manager.get_text("bot_management.shutdown_confirm_title", interaction.user.id, interaction.guild_id)
    desc_text = translation_manager.get_text("bot_management.shutdown_confirm_desc", interaction.user.id, interaction.guild_id)
    embed = Embed(
        title=title_text,
        description=desc_text,
        color=0xff5555
    )
    
    class ConfirmShutdown(View):
        def __init__(self):
            super().__init__(timeout=60) 

        @button(label=translation_manager.get_text("buttons.yes", interaction.user.id, interaction.guild_id), style=ButtonStyle.danger)
        async def confirm(self, interaction2: Interaction, button: Button):
            if interaction2.user.id != interaction.user.id:
                cannot_confirm_msg = translation_manager.get_text("bot_management.cannot_confirm", interaction2.user.id, interaction2.guild_id)
                return await interaction2.response.send_message(cannot_confirm_msg, ephemeral=True)
            shutting_down_msg = translation_manager.get_text("bot_management.shutting_down", interaction2.user.id, interaction2.guild_id)
            await interaction2.response.edit_message(content=shutting_down_msg, embed=None, view=None)
            changelog_channels = load_channel_data("changelog")
            title_offline = translation_manager.get_text("bot_management.bot_offline")
            embed = discord.Embed(
                title=title_offline,
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            for guild_id, channel_id in changelog_channels.items():
                guild = bot.get_guild(int(guild_id))
                if guild:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.send(embed=embed)
                        except Exception as e:
                            error_msg = translation_manager.get_text("tempmail.send_shutdown_error", guild_id=guild_id, error=str(e))
                            print(error_msg)
            await bot.close()

        @button(label=translation_manager.get_text("buttons.no", interaction.user.id, interaction.guild_id), style=ButtonStyle.secondary)
        async def cancel(self, interaction2: Interaction, button: Button):
            if interaction2.user.id != interaction.user.id:
                cannot_cancel_msg = translation_manager.get_text("bot_management.cannot_cancel", interaction2.user.id, interaction2.guild_id)
                return await interaction2.response.send_message(cannot_cancel_msg, ephemeral=True)
            cancelled_msg = translation_manager.get_text("bot_management.shutdown_cancelled", interaction2.user.id, interaction2.guild_id)
            await interaction2.response.edit_message(content=cancelled_msg, embed=None, view=None)
    
    await interaction.response.send_message(
        embed=embed, view=ConfirmShutdown(), ephemeral=True
    )

@app_commands.guild_only()
@bot.tree.command(name="faq", description=get_command_description("faq"))
async def faq(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild_id
    
    title = translation_manager.get_text("faq.title", user_id, guild_id)
    embed = discord.Embed(title=title, color=discord.Color.green())
    
    unlock_bot_name = translation_manager.get_text("faq.unlock_bot", user_id, guild_id)
    unlock_bot_value = translation_manager.get_text("faq.unlock_bot_answer", user_id, guild_id)
    embed.add_field(name=unlock_bot_name, value=unlock_bot_value, inline=False)
    
    unlock_ai_name = translation_manager.get_text("faq.unlock_ai", user_id, guild_id)
    unlock_ai_value = translation_manager.get_text("faq.unlock_ai_answer", user_id, guild_id)
    embed.add_field(name=unlock_ai_name, value=unlock_ai_value, inline=False)
    
    commands_name = translation_manager.get_text("faq.bot_commands", user_id, guild_id)
    commands_value = translation_manager.get_text("faq.bot_commands_answer", user_id, guild_id)
    embed.add_field(name=commands_name, value=commands_value, inline=False)
    
    footer_text = translation_manager.get_text("faq.footer", user_id, guild_id)
    embed.set_footer(text=footer_text)

    view = ui.View()

    class FAQButtons(ui.View):
        @ui.button(label=translation_manager.get_text("buttons.yes_send_ai_faq", interaction.user.id, interaction.guild_id), style=ButtonStyle.success)
        async def yes(self, interaction2: Interaction, _):
            if not interaction.user.guild_permissions.administrator:
                no_perms = translation_manager.get_text("general.no_permissions_short", interaction2.user.id, interaction2.guild_id)
                return await interaction2.response.send_message(no_perms, ephemeral=True)

            title = translation_manager.get_text("faq.full_faq_title", interaction2.user.id, interaction2.guild_id)
            description = translation_manager.get_text("faq.full_faq_description", interaction2.user.id, interaction2.guild_id)
            full = discord.Embed(title=title, description=description, color=discord.Color.blurple())
            
            who_name = translation_manager.get_text("faq.who_is_koto", interaction2.user.id, interaction2.guild_id)
            who_value = translation_manager.get_text("faq.who_is_koto_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(name=who_name, value=who_value, inline=False)

            what_name = translation_manager.get_text("faq.what_can_do", interaction2.user.id, interaction2.guild_id)
            what_value = translation_manager.get_text("faq.what_can_do_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(name=what_name, value=what_value, inline=False)

            memory_name = translation_manager.get_text("faq.memory", interaction2.user.id, interaction2.guild_id)
            memory_value = translation_manager.get_text("faq.memory_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(name=memory_name, value=memory_value, inline=False)

            commands_name = translation_manager.get_text("faq.commands", interaction2.user.id, interaction2.guild_id)
            commands_value = translation_manager.get_text("faq.commands_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(name=commands_name, value=commands_value, inline=False)

            mute_name = translation_manager.get_text("faq.mute", interaction2.user.id, interaction2.guild_id)
            mute_value = translation_manager.get_text("faq.mute_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(name=mute_name, value=mute_value, inline=False)

            servers_name = translation_manager.get_text("faq.other_servers", interaction2.user.id, interaction2.guild_id)
            servers_value = translation_manager.get_text("faq.other_servers_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(name=servers_name, value=servers_value, inline=False)

            weird_response_name = translation_manager.get_text("faq.weird_response", interaction2.user.id, interaction2.guild_id)
            weird_response_value = translation_manager.get_text("faq.weird_response_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(
                name=weird_response_name,
                value=weird_response_value,
                inline=False
            )

            add_to_server_name = translation_manager.get_text("faq.add_to_server", interaction2.user.id, interaction2.guild_id)
            add_to_server_value = translation_manager.get_text("faq.add_to_server_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(
                name=add_to_server_name,
                value=add_to_server_value,
                inline=False
            )

            dm_name = translation_manager.get_text("faq.dm", interaction2.user.id, interaction2.guild_id)
            dm_value = translation_manager.get_text("faq.dm_answer", interaction2.user.id, interaction2.guild_id)
            full.add_field(
                name=dm_name,
                value=dm_value,
                inline=False
            )

            full.set_footer(text=translation_manager.get_text("faq.dm_footer", interaction2.user.id, interaction2.guild_id))
            await interaction2.channel.send(embed=full)
            await interaction2.response.defer()

        @ui.button(label=translation_manager.get_text("buttons.no", interaction.user.id, interaction.guild_id), style=ButtonStyle.secondary)
        async def no(self, interaction2: Interaction, _):
            close_msg = translation_manager.get_text("faq.close_faq", interaction2.user.id, interaction2.guild_id)
            await interaction2.response.send_message(close_msg, ephemeral=True)

    await interaction.response.send_message(embed=embed, ephemeral=True, view=FAQButtons())

# ==================== TEST COMMAND ====================
@bot.tree.command(name="testlang", description=get_command_description("testlang"))
async def test_lang(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild_id
    
    # Test basic translation
    test_text = translation_manager.get_text("general.success", user_id, guild_id)
    help_title = translation_manager.get_text("help.title", user_id, guild_id)
    faq_title = translation_manager.get_text("faq.title", user_id, guild_id)
    
    # Show current language
    current_lang = translation_manager.get_user_language(user_id, guild_id)
    
    test_title = translation_manager.get_text("test.translation_title", user_id, guild_id)
    embed = discord.Embed(title=test_title, color=discord.Color.blue())
    current_lang_text = translation_manager.get_text("general.current_language", user_id, guild_id)
    embed.add_field(name=current_lang_text, value=current_lang, inline=False)
    success_text = translation_manager.get_text("general.success", interaction.user.id, interaction.guild_id)
    embed.add_field(name=success_text, value=test_text, inline=False)
    help_title_label = translation_manager.get_text("help.title", interaction.user.id, interaction.guild_id)
    embed.add_field(name=help_title_label, value=help_title, inline=False)
    faq_title_label = translation_manager.get_text("faq.title", interaction.user.id, interaction.guild_id)
    embed.add_field(name=faq_title_label, value=faq_title, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)



@bot.tree.command(name="clear", description=get_command_description("clear"))
@app_commands.checks.has_permissions(administrator=True)
async def clear(interaction: discord.Interaction):
    if interaction.user.id not in AUTHORIZED_USERS:
        msg = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(msg, ephemeral=True)
    guild_id = str(interaction.guild_id)
    history_file = get_ai_history_file(guild_id)
    if os.path.exists(history_file):
        os.remove(history_file)
    msg = translation_manager.get_text("ai.history_cleared", interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(msg, ephemeral=True)
    await log_action(interaction.guild, "ClearAIHistory", interaction.user, executor=interaction.user)

# @bot.tree.command(name="whack", description="Wyczyść historię i zresetuj AI (admin only)")
# @app_commands.checks.has_permissions(administrator=True)
# async def whack(interaction: discord.Interaction):
#     if not await check_changelog_and_module(interaction, "ai"):
#         return
#     guild_id = str(interaction.guild_id)
#     history_file = get_ai_history_file(guild_id)
#     user_map_file = get_ai_user_map_file(guild_id)
#     if os.path.exists(history_file):
#         os.remove(history_file)
#     if os.path.exists(user_map_file):
#         os.remove(user_map_file)
#     await interaction.response.send_message("Historia AI i mapowanie użytkowników zostały wyczyszczone.", ephemeral=True)
#     await log_action(interaction.guild, "WhackAI", interaction.user, executor=interaction.user)

@app_commands.guild_only()
@bot.tree.command(name="refreshusers", description=get_command_description("refreshusers"))
@app_commands.checks.has_permissions(administrator=True)
async def refreshusers(interaction: discord.Interaction):
    if not await check_changelog_and_module(interaction, "ai"):
        return
    guild = interaction.guild
    user_map = {}
    async for member in guild.fetch_members(limit=None):
        if not member.bot:
            user_map[member.name] = str(member.id)
    save_ai_user_map(guild.id, user_map)
    msg = translation_manager.get_text("ai.user_mapping_refreshed", interaction.user.id, interaction.guild_id, count=len(user_map))
    await interaction.response.send_message(msg, ephemeral=True)
    await log_action(interaction.guild, "RefreshAIUsers", interaction.user, executor=interaction.user)

@bot.tree.command(name="language", description=get_command_description("language"))
@app_commands.describe(
    language=get_parameter_description("language"),
    scope=get_parameter_description("scope")
)
@app_commands.choices(language=[
    app_commands.Choice(name=translation_manager.get_text("language.polish_choice", None, None), value="pl"),
    app_commands.Choice(name=translation_manager.get_text("language.english_choice", None, None), value="en")
])
@app_commands.choices(scope=[
    app_commands.Choice(name=translation_manager.get_text("language.personal_choice", None, None), value="personal"),
    app_commands.Choice(name=translation_manager.get_text("language.server_choice", None, None), value="server")
])
async def language_command(interaction: discord.Interaction, language: str, scope: str = "personal"):
    user_id = interaction.user.id
    guild_id = interaction.guild_id if interaction.guild else None
    
    print(f"🔧 Language command: user {user_id}, guild {guild_id}, language {language}, scope {scope}")
    
    if scope == "server":
        if not interaction.user.guild_permissions.administrator:
            no_perms_text = translation_manager.get_text("general.no_permissions", user_id, guild_id)
            return await interaction.response.send_message(no_perms_text, ephemeral=True)
        
        if guild_id:
            translation_manager.set_guild_language(guild_id, language)
            print(f"🌐 Set guild {guild_id} language to {language}")
            success_text = translation_manager.get_text(f"language.server_set_{language}", user_id, guild_id)
        else:
            error_text = translation_manager.get_text("language.dm_server_scope_error", user_id, guild_id)
            return await interaction.response.send_message(error_text, ephemeral=True)
    else:
        translation_manager.set_user_language(user_id, language)
        print(f"👤 Set user {user_id} language to {language}")
        success_text = translation_manager.get_text(f"language.personal_set_{language}", user_id, guild_id)
    
    await interaction.response.send_message(success_text, ephemeral=True)

# ==================== MUSIC SYSTEM ====================
YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'extract_audio': True,
    'audio_format': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'source_address': '0.0.0.0',
    'prefer_ffmpeg': True,
    'postprocessor_args': ['-threads', '4'],
    'socket_timeout': 10
}

FFMPEG_PATH = os.path.join(os.path.dirname(__file__), 'ffmpeg_new', 'bin', 'ffmpeg.exe')

if not os.path.exists(FFMPEG_PATH):
    print(translation_manager.get_text("errors.ffmpeg_not_found", None, None, path=FFMPEG_PATH))
else:
    ffmpeg_found_msg = translation_manager.get_text("messages.ffmpeg_found", None, None, path=FFMPEG_PATH)
    print(ffmpeg_found_msg)

FFMPEG_OPTIONS = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 10000000 -probesize 10000000',
    'options': '-vn -bufsize 128k -maxrate 128k -ab 128k'
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)

spotify = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
     client_id="293aaafd9715473b95b8c9d93f43047a",
     client_secret="76f8708137854c3db82a5d2bdd57470b"
))

class MusicPlayer:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = []
        self.current_track = None
        self.voice_client = None
        self.text_channel = None
        self.control_message = None
        self.volume = 0.5
        self.is_playing = False
        self.is_paused = False
        self.disconnect_timer = None
        self.last_activity = datetime.now()

    def add_track(self, track_info):
        track_data = {
            'url': track_info.get('url', ''),
            'title': track_info.get('title', 'Unknown'),
            'duration': track_info.get('duration', 0),
            'requester': track_info.get('requester'),
            'webpage_url': track_info.get('webpage_url', '')
        }
        self.queue.append(track_data)

    def get_next_track(self):
        return self.queue[0] if self.queue else None

    def shuffle_queue(self):
        if len(self.queue) > 1:
            random.shuffle(self.queue)

    async def schedule_disconnect(self):
        if self.disconnect_timer:
            self.disconnect_timer.cancel()

        self.disconnect_timer = asyncio.create_task(self._disconnect_after_delay())

    async def _disconnect_after_delay(self):
        await asyncio.sleep(60)
        if not self.queue and not self.is_playing:
            await stop_music(self)

music_players = {}

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume=volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True, volume=0.5):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            sources = [cls._create_source(entry, stream, volume) for entry in data['entries'] if entry]
            return [s for s in sources if s is not None]
        else:
            source = cls._create_source(data, stream, volume)
            return [source] if source is not None else []

    @classmethod
    def _create_source(cls, data, stream, volume):
        try:
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            ffmpeg = discord.FFmpegPCMAudio(
                filename,
                executable=FFMPEG_PATH,
                **FFMPEG_OPTIONS
            )
            return cls(ffmpeg, data=data, volume=volume)
        except Exception as e:
            print(f"Error creating audio source: {e}")
            return None

def get_music_player(guild_id):
    if guild_id not in music_players:
        music_players[guild_id] = MusicPlayer(guild_id)
    return music_players[guild_id]

# ==================== CHANNEL MANAGEMENT ====================
guild_invites: dict[int, list] = {}
message_store: dict[int, dict[str, dict]] = {}
BASE_LOG_DIR = 'message_logs'
os.makedirs(BASE_LOG_DIR, exist_ok=True)

RR_DIR = 'reaction_roles'
os.makedirs(RR_DIR, exist_ok=True)

def _rr_path(guild_id: int) -> str:
    return os.path.join(RR_DIR, f"{guild_id}.json")

def load_reaction_roles(guild_id: int) -> dict:
    path = _rr_path(guild_id)
    if not os.path.exists(path):
        return {}
    with open(path, 'r') as f:
        return json.load(f)

def save_reaction_roles(guild_id: int, data: dict):
    path = _rr_path(guild_id)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def ensure_guild_log_dir(guild_id: int) -> str:
    path = os.path.join(BASE_LOG_DIR, str(guild_id))
    os.makedirs(path, exist_ok=True)
    return path

ACTION_SETTINGS = {
    "SetPerms": {"key": "logging.set_perms", "color": discord.Color.gold()},
    "RevokePerms": {"key": "logging.revoke_perms", "color": discord.Color.dark_gold()},
    "SetLogChannel": {"key": "logging.set_log_channel", "color": discord.Color.blue()},
    "SetWelcomeChannel": {"key": "logging.set_welcome_channel", "color": discord.Color.green()},
    "SetPingChannel": {"key": "logging.set_ping_channel", "color": discord.Color.dark_green()},
    "SetCounterChannel": {"key": "logging.set_counter_channel", "color": discord.Color.purple()},
    "SetAIChannel": {"key": "logging.set_ai_channel", "color": discord.Color.blurple()},
    "SetChangelogChannel": {"key": "logging.set_changelog_channel", "color": discord.Color.dark_purple()},
    "Lockdown": {"key": "logging.lockdown", "color": discord.Color.orange()},
    "Unlock": {"key": "logging.unlock", "color": discord.Color.green()},
    "Purge": {"key": "logging.purge", "color": discord.Color.teal()},
    "Ban": {"key": "logging.ban", "color": discord.Color.red()},
    "Unban": {"key": "logging.unban", "color": discord.Color.green()},
    "Kick": {"key": "logging.kick", "color": discord.Color.red()},
    "Timeout": {"key": "logging.timeout", "color": discord.Color.dark_grey()},
    "Warning": {"key": "logging.warning", "color": discord.Color.orange()},
    "ClearWarnings": {"key": "logging.clear_warnings", "color": discord.Color.dark_teal()},
    "ClearChannels": {"key": "logging.clear_channels", "color": discord.Color.dark_blue()},
    "MessageDelete": {"key": "logging.message_delete", "color": discord.Color.dark_red()},
    "MessageEdit": {"key": "logging.message_edit", "color": discord.Color.orange()},
    "ChannelCreate": {"key": "logging.channel_create", "color": discord.Color.green()},
    "ChannelDelete": {"key": "logging.channel_delete", "color": discord.Color.red()},
    "ChannelUpdate": {"key": "logging.channel_update", "color": discord.Color.gold()},
    "RoleCreate": {"key": "logging.role_create", "color": discord.Color.green()},
    "RoleDelete": {"key": "logging.role_delete", "color": discord.Color.red()},
    "RoleAdd": {"key": "logging.role_add", "color": discord.Color.blue()},
    "RoleRemove": {"key": "logging.role_remove", "color": discord.Color.dark_grey()},
    "MemberJoin": {"key": "logging.member_join", "color": discord.Color.green()},
    "MemberLeave": {"key": "logging.member_leave", "color": discord.Color.dark_red()},
    "Changelog": {"title": "Changelog", "color": discord.Color.gold()},
    "Announcement": {"title": "Announcement", "color": discord.Color.blue()}
}

os.makedirs('channels', exist_ok=True)
os.makedirs('logs', exist_ok=True)
os.makedirs('warns', exist_ok=True)
os.makedirs('counters', exist_ok=True)
os.makedirs('config', exist_ok=True)
os.makedirs('economy', exist_ok=True)

for fname in ('counters','loggers','welcomers','pingers','changelog','econlogs'):
    path = f'channels/{fname}.json'
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump({}, f)

def get_config_path(guild_id): return f'config/{guild_id}.json'
def load_config(guild_id):
    path = get_config_path(guild_id)
    if not os.path.exists(path):
        with open(path,'w') as f:
            json.dump({"permissions": {}}, f)
    with open(path,'r') as f:
        return json.load(f)

def save_config(guild_id, data):
    with open(get_config_path(guild_id),'w') as f:
        json.dump(data, f)

def get_log_path(guild_id): return f'logs/{guild_id}.json'
def append_moderation_log(guild_id, entry):
    path = get_log_path(guild_id)
    logs = []
    if os.path.exists(path):
        with open(path,'r', encoding='utf-8') as f:
            logs = json.load(f)
    logs.append(entry)
    with open(path,'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=2)
        f.write('\n')

def get_warns_path(guild_id, user_id):
    dirpath = f'warns/{guild_id}'
    os.makedirs(dirpath, exist_ok=True)
    return f'{dirpath}/{user_id}.json'

def load_user_warns(guild_id, user_id):
    path = get_warns_path(guild_id, user_id)
    if not os.path.exists(path):
        return []
    with open(path,'r') as f:
        return json.load(f)

def save_user_warns(guild_id, user_id, warns):
    path = get_warns_path(guild_id, user_id)
    with open(path,'w') as f:
        json.dump(warns, f)

def get_counter_path(guild_id): return f'counters/{guild_id}.json'
def load_counter_data(guild_id):
    path = get_counter_path(guild_id)
    if not os.path.exists(path):
        return {"last_number": 0, "last_user": None}
    with open(path,'r') as f:
        return json.load(f)

def save_counter_data(guild_id, data):
    with open(get_counter_path(guild_id),'w') as f:
        json.dump(data, f)

def load_channel_data(name):
    with open(f'channels/{name}.json','r') as f:
        return json.load(f)

def save_channel_data(name, data):
    with open(f'channels/{name}.json','w') as f:
        json.dump(data, f)

def has_permission(ctx, perm):
    if ctx.user.guild_permissions.administrator:
        return True
    cfg = load_config(str(ctx.guild.id))
    for role_id, perms in cfg.get("permissions", {}).items():
        if int(role_id) in [r.id for r in ctx.user.roles] and perm in perms:
            return True
    return False

async def log_action(guild, action_type, user, reason=None, duration=None, executor=None, fields=None):
    settings = ACTION_SETTINGS.get(action_type, {"key": f"logging.{action_type.lower()}", "color": discord.Color.default()})
    log_channels = load_channel_data("loggers")
    ch_id = log_channels.get(str(guild.id))
    if ch_id:
        ch = guild.get_channel(int(ch_id))
        if ch:
            # Get title from translations
            title = translation_manager.get_text(settings["key"], None, guild.id) if "key" in settings else settings.get("title", action_type)
            embed = discord.Embed(title=title, color=settings["color"])
            
            # Get translated field names
            executor_text = translation_manager.get_text("general.executor", None, guild.id)
            user_text = translation_manager.get_text("general.user", None, guild.id)
            unknown_text = translation_manager.get_text("general.unknown_user", None, guild.id)
            reason_text = translation_manager.get_text("general.reason", None, guild.id)
            duration_text = translation_manager.get_text("general.duration", None, guild.id)
            
            if executor:
                embed.add_field(name=executor_text, value=f"{executor} ({executor.id})", inline=False)
            if user is not None and hasattr(user, "id"):
                embed.add_field(name=user_text, value=f"{user} ({user.id})", inline=False)
            else:
                embed.add_field(name=user_text, value=unknown_text, inline=False)
            if fields:
                for f in fields:
                    embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
            else:
                if reason:
                    embed.add_field(name=reason_text, value=reason, inline=False)
                if duration:
                    embed.add_field(name=duration_text, value=duration, inline=False)
            embed.set_footer(text=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            await ch.send(embed=embed)
    entry = {"action": action_type, "user_id": user.id if hasattr(user, "id") else None, "executor_id": executor.id if executor else None, "reason": reason, "duration": duration, "fields": fields, "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}
    append_moderation_log(str(guild.id), entry)

# ==================== ECONOMY SYSTEM ====================
def log_econ(guild_id, msg, user_id=None):
    import datetime
    now = datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if user_id is not None:
        user = get_user_eco(guild_id, user_id)
        status = f" | balance: {user.get('balance',0)}$ | bank: {user.get('bank',0)}$"
    else:
        status = ""
    msg_final = f"{msg}{status}"

    log_path = f"economy/{guild_id}_logs.json"
    log_entry = {"time": now, "msg": msg_final}
    logs = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf8") as f:
            try: logs = json.load(f)
            except: logs = []
    logs.append(log_entry)
    with open(log_path, "w", encoding="utf8") as f:
        json.dump(logs[-500:], f, ensure_ascii=False, indent=2)

    econlog_cfg = "channels/econlogs.json"
    if os.path.exists(econlog_cfg):
        with open(econlog_cfg, "r") as f:
            econlogs = json.load(f)
        ch_id = econlogs.get(str(guild_id))
        if ch_id:
            channel = bot.get_channel(ch_id)
            if channel:
                try:
                    asyncio.create_task(channel.send(f"`[{now}]` {msg_final}"))
                except: pass

def _eco_path(guild_id):
    return os.path.join('economy', f'{guild_id}.json')

def load_economy(guild_id):
    path = _eco_path(guild_id)
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump({'users': {}, 'incomes': {}, 'work': [10, 100]}, f)
    with open(path, 'r') as f:
        return json.load(f)

def save_economy(guild_id, data):
    with open(_eco_path(guild_id), 'w') as f:
        json.dump(data, f)

def get_user_eco(guild_id, user_id):
    eco = load_economy(guild_id)
    user = eco['users'].get(str(user_id), {'balance': 0, 'bank': 0, 'last_daily': 0, 'last_work': 0, 'last_steal': 0, 'in_game': False})
    eco['users'][str(user_id)] = user
    save_economy(guild_id, eco)
    return user

def update_user_eco(guild_id, user_id, update):
    eco = load_economy(guild_id)
    eco['users'][str(user_id)] = update
    save_economy(guild_id, eco)

def eco_error(msg):
    return discord.Embed(description=f"❌ {msg}", color=discord.Color.red())

def eco_success(msg):
    return discord.Embed(description=f"✅ {msg}", color=discord.Color.green())

def is_admin(member):
    return member.guild_permissions.administrator

def role_income_sum(guild, user, eco):
    roles = user.roles
    role_incomes = eco['incomes']
    found = []
    total = 0
    for r in roles:
        if str(r.id) in role_incomes:
            found.append((r, role_incomes[str(r.id)]))
            total += role_incomes[str(r.id)]
    return total, found

# ==================== TASKS ====================
@tasks.loop(seconds=60)
async def check_new_mail():
    logging.info("Checking for new emails...")
    for user_id, sess in list(user_sessions.items()):
        logging.info(f"Checking user id {user_id}")
        addr = sess["address"]
        try:
            resp = requests.get(f"{TEMPMAIL_API}/get_messages", params={"address": addr}, timeout=5)
            resp.raise_for_status()
            messages = resp.json()
        except Exception as e:
            logging.error(translation_manager.get_text("logging.error_polling", user_id, None, address=addr, error=str(e)))
            continue

        last_checked = sess.get("last_checked")
        new_msgs = [m for m in messages if not last_checked or m['received_at'] > last_checked]
        if new_msgs:
            logging.info(translation_manager.get_text("logging.new_message", user_id, None, user=user_id, address=addr, count=len(new_msgs)))
            try:
                user = await bot.fetch_user(int(user_id))
                for m in new_msgs:
                    try:
                        mail_body = m.get('content') or m.get('body', '')
                        no_content = translation_manager.get_text("tempmail.no_content", user_id, None)
                        new_email_title = translation_manager.get_text("tempmail.new_email_title", user_id, None, address=addr)
                        embed = discord.Embed(
                            title=new_email_title,
                            description=mail_body[:4096] or no_content,
                            color=discord.Color.blue()
                        )
                        from_text = translation_manager.get_text("tempmail.from_label", user_id, None)
                        subject_text = translation_manager.get_text("tempmail.subject_label", user_id, None)
                        unknown_sender = translation_manager.get_text("tempmail.unknown_sender", user_id, None)
                        no_subject = translation_manager.get_text("tempmail.no_subject", user_id, None)
                        embed.add_field(name=from_text, value=m.get('sender', unknown_sender), inline=True)
                        embed.add_field(name=subject_text, value=m.get('subject', no_subject), inline=True)
                        if "received_at" in m:
                            try:
                                embed.timestamp = datetime.fromisoformat(m["received_at"])
                            except Exception:
                                pass
                        await user.send(embed=embed)
                        logging.info(translation_manager.get_text("logging.mail_sent", user_id, None, user=user_id, subject=m.get('subject', '')))
                    except Exception as e:
                        logging.error(f"DM send failed for {user_id}: {e}")
                user_sessions[user_id]["last_checked"] = max(m['received_at'] for m in new_msgs)
            except Exception as e:
                logging.error(translation_manager.get_text("logging.dm_failed", user_id, None, user=user_id, error=str(e)))

        # Check expiry
        if sess["expires"] and datetime.now(timezone.utc).timestamp() > sess["expires"]:
            logging.info(translation_manager.get_text("logging.session_expired", user_id, None, user=user_id, address=addr))
            del user_sessions[user_id]

@tasks.loop(hours=1)
async def expire_warns():
    now = datetime.now(timezone.utc)
    base = 'warns'
    if not os.path.exists(base):
        return
    for guild_id in os.listdir(base):
        guild_path = os.path.join(base, guild_id)
        if not os.path.isdir(guild_path):
            continue
        for fname in os.listdir(guild_path):
            if not fname.endswith('.json'):
                continue
            user_id = fname[:-5]
            path = os.path.join(guild_path, fname)
            warns = load_user_warns(guild_id, user_id)
            changed = False
            new_warns = []
            for w in warns:
                ts = datetime.strptime(w['timestamp'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                if now - ts < timedelta(days=2):
                    new_warns.append(w)
                else:
                    changed = True
            if changed:
                if new_warns:
                    save_user_warns(guild_id, user_id, new_warns)
                else:
                    os.remove(path)

@tasks.loop(hours=24)
async def cleanup_message_logs():
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    if not os.path.exists(BASE_LOG_DIR):
        return
    for guild_id in os.listdir(BASE_LOG_DIR):
        dirpath = os.path.join(BASE_LOG_DIR, guild_id)
        if not os.path.isdir(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.endswith('.json'):
                continue
            full = os.path.join(dirpath, fname)
            with open(full, 'r', encoding='utf-8') as f:
                entries = json.load(f)
            filtered = [e for e in entries if datetime.fromisoformat(e["timestamp"]) >= cutoff]
            if len(filtered) != len(entries):
                with open(full, 'w', encoding='utf-8') as f:
                    json.dump(filtered, f, ensure_ascii=False, indent=2)

# ==================== BOT EVENTS ====================
async def save_guilds_periodically(bot):
    while True:
        with open("guilds.json", "w", encoding="utf-8") as f:
            json.dump([{"id": str(g.id), "name": g.name} for g in bot.guilds], f, ensure_ascii=False)
        await asyncio.sleep(30)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if bot_locked:
        try:
            locked_msg = translation_manager.get_text("bot_management.bot_locked", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(locked_msg, ephemeral=True)
        except:
            pass
        return


@bot.event
async def on_ready():
    global bot_locked
    # Using default language for startup logs since no user context
    logged_msg = translation_manager.get_text("logging.logged_as", name=bot.user.name, id=bot.user.id)
    print(logged_msg)
    expire_warns.start()
    cleanup_message_logs.start()
    bot_locked = False
    for guild in bot.guilds:
        log_dir = ensure_guild_log_dir(guild.id)
        path_main = os.path.join(log_dir, f'{guild.id}_main.json')
        if os.path.isfile(path_main):
            data = []
            with open(path_main, 'r', encoding='utf-8') as f:
                data = json.load(f)
            message_store[guild.id] = {str(e["message_id"]): {"author_id": e["author_id"], "content": e["content"]} for e in data}
        else:
            message_store[guild.id] = {}
    try:
        synced = await bot.tree.sync()
        sync_msg = translation_manager.get_text("logging.synced_commands", count=len(synced))
        print(sync_msg)
    except Exception:
        pass

    if not check_new_mail.is_running():
        check_new_mail.start()
    await init_sessions_from_backend()

    changelog_channels = load_channel_data("changelog")
    title_text = translation_manager.get_text("bot_management.bot_online")
    embed = discord.Embed(
        title=title_text,
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )

    for guild_id, channel_id in changelog_channels.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    error_msg = translation_manager.get_text("tempmail.send_startup_error", guild_id=guild_id, error=str(e))
                    print(error_msg)

    bot.loop.create_task(save_guilds_periodically(bot))

@bot.event
async def on_guild_join(guild):
    if bot_locked:
        return
    try:
        # Fetch current config from panel
        resp = requests.get(module_manager.panel_url, timeout=5)
        config = resp.json() if resp.status_code == 200 else {"global_disabled": [], "server_disabled": {}}
        gid = str(guild.id)
        if gid not in config["server_disabled"]:
            config["server_disabled"][gid] = []
        if "ai" not in config["server_disabled"][gid]:
            config["server_disabled"][gid].append("ai")
        # Push updated config to panel
        requests.post(module_manager.panel_url.replace('/api/modules', '/api/modules_update'), json=config, timeout=5)

        requests.post(
            "https://bot.takiekoksy.pl/api/guilds_update",
            json={"id": gid, "name": guild.name},
            timeout=5
        )

    except Exception as e:
        error_msg = translation_manager.get_text("tempmail.block_ai_new_server_error", error=str(e))
        print(f"[on_guild_join] {error_msg}")


    channel = None
    for c in guild.text_channels:
        if c.permissions_for(guild.me).send_messages:
            channel = c
            break
    if channel is not None:
        title_text = translation_manager.get_text("welcome.title")
        description_text = translation_manager.get_text("welcome.description")
        footer_text = translation_manager.get_text("welcome.footer")
        
        embed = discord.Embed(
            title=title_text,
            description=description_text,
            color=discord.Color.blurple()
        )
        embed.set_footer(text=footer_text)
        await channel.send(embed=embed)
    else:
        error_msg = translation_manager.get_text("tempmail.welcome_send_error", guild_id=guild.id)
        print(error_msg)

@bot.event
async def on_message(message):
    global indexNumber
    if message.author == bot.user:
        return
    if bot_locked:
        return

    # Handle DM messages for AI
    if isinstance(message.channel, discord.DMChannel):
        if module_manager.is_module_enabled(None, "ai"):
            await handle_ai_dm_message(message)
        return

    # Economy system - give money for messages
    if module_manager.is_module_enabled(message.guild.id, "economy"):
        user = get_user_eco(message.guild.id, message.author.id)
        user['balance'] += 1
        update_user_eco(message.guild.id, message.author.id, user)
        log_msg = translation_manager.get_text("logging.economy_log", message.author.id, message.guild.id, 
                                              name=message.author.name, display_name=message.author.display_name, id=message.author.id)
        log_econ(message.guild.id, log_msg, message.author.id)

    # Handle AI channel messages
    ai_config = load_ai_channel_config()
    ai_channel_id = ai_config.get(str(message.guild.id))

    if ai_channel_id and message.channel.id == ai_channel_id and module_manager.is_module_enabled(message.guild.id, "ai"):
        if bot_locked_per_guild.get(message.guild.id, False):
            return
        await handle_ai_server_message(message)

    await bot.process_commands(message)

    # Message logging
    log_dir = ensure_guild_log_dir(message.guild.id)
    entry = {
        "message_id": message.id,
        "channel_id": message.channel.id,
        "author_id": message.author.id,
        "content": message.content,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    message_store.setdefault(message.guild.id, {})[str(message.id)] = {"author_id": message.author.id, "content": message.content}
    path_main = os.path.join(log_dir, f'{message.guild.id}_main.json')
    logs = []
    if os.path.exists(path_main):
        with open(path_main, 'r', encoding='utf-8') as f:
            logs = json.load(f)
    logs.append(entry)
    with open(path_main, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

    # Counter channel handling
    gid = str(message.guild.id)
    ctr = load_channel_data("counters").get(gid)
    if ctr and message.channel.id == int(ctr):
        data = load_counter_data(gid)
        try:
            num = int(message.content.strip())
            if num != data["last_number"] + 1:
                await message.delete()
                error_msg = translation_manager.get_text("counter.wrong_number", message.author.id, message.guild.id, number=data['last_number']+1)
                await message.author.send(error_msg)
                return
            if data["last_user"] == message.author.id:
                await message.delete()
                wait_msg = translation_manager.get_text("counter.wait_turn", message.author.id, message.guild.id)
                await message.author.send(wait_msg)
                return
            data["last_number"] = num
            data["last_user"] = message.author.id
            save_counter_data(gid, data)
        except ValueError:
            await message.delete()
            error_msg = translation_manager.get_text("counter.not_number", message.author.id, message.guild.id)
            await message.author.send(error_msg)

async def handle_ai_dm_message(message):
    if bot_locked:
        return
    global indexNumber

    try:
        await message.channel.typing()
    except:
        pass

    if len(message.content) > 300:
        reply_text = translation_manager.get_text("ai.too_long_message", message.author.id)
        await message.reply(reply_text)
        return

    user_id = message.author.id
    history = load_ai_dm_history(user_id)

    history.append({"id": indexNumber, "role": "user", "content": f"{message.author.name}: {message.content}"})
    indexNumber += 1

    if len(history) > 20:
        history = history[-20:]

    messages = [{"role": "system", "content": ai_prompt}] + history

    try:
        reply = await ask_ai(messages, message.author.id)
    except Exception as e:
        print(f"[on_message] ask_ai failed: {e}", file=sys.stderr)
        error_text = translation_manager.get_text("ai.something_went_wrong", message.author.id)
        await message.reply(error_text)
        return

    reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
    reply2 = reply

    print(repr(reply))
    if "\n" in reply:
        thinking_text = translation_manager.get_text("ai.too_much_thinking", message.author.id)
        reply = thinking_text
        reply2 = thinking_text

    await message.reply(reply)

    history.append({"role": "assistant", "content": reply2})

    save_ai_dm_history(user_id, history)

    training_data_path = os.path.join("data", "training_data.jsonl")
    os.makedirs(os.path.dirname(training_data_path), exist_ok=True)
    with open(training_data_path, "a", encoding="utf-8") as f:
        training_format = translation_manager.get_text("ai.training_format", None, None, 
                                                      author=message.author.name, 
                                                      content=message.content, 
                                                      reply=reply2)
        json.dump({
            "conversation": training_format
        }, f, ensure_ascii=False)
        f.write("\n")

async def handle_ai_server_message(message):
    if bot_locked:
        return
    global indexNumber

    try:
        await message.channel.typing()
    except:
        pass

    if len(message.content) > 300:
        reply_text = translation_manager.get_text("ai.too_long_message", message.author.id, message.guild.id)
        await message.reply(reply_text)
        return

    history = load_ai_history(message.guild.id)
    user_map = load_ai_user_map(message.guild.id)
    updated = False
    author_id = str(message.author.id)

    if message.author.name not in user_map:
        user_map[message.author.name] = author_id
        updated = True
    if updated:
        save_ai_user_map(message.guild.id, user_map)

    isMentioned = False
    wstaw = []

    if message.content:
        for text in message.content.split():
            print(text)
            tempText = text
            for name in sorted(user_map.keys(), key=lambda x: -len(x)):
                pattern = r'\b' + re.escape(name) + r'\b'
                tempText = re.sub(pattern, user_map[name], tempText)
                if tempText != text:
                    history.append({"id": indexNumber, "role": "user", "content": f"{message.author.name}: {message.content}", "mention": text})
                    wstaw = [{"id": indexNumber, "role": "user", "content": f"{message.author.name}: {message.content}", "mention": text}]
                    indexNumber += 1
                    isMentioned = True
                    break
            if isMentioned:
                break

    if not isMentioned:
        history.append({"id": indexNumber, "role": "user", "content": f"{message.author.name}: {message.content}"})
        wstaw = [{"id": indexNumber, "role": "user", "content": f"{message.author.name}: {message.content}"}]
        indexNumber += 1

    print(wstaw)

    if len(history) > 20:
        history = history[-20:]

    messages = [{"role": "system", "content": ai_prompt}] + history

    try:
        reply = await ask_ai(messages, message.author.id, message.guild.id)
    except Exception as e:
        print(f"[on_message] ask_ai failed: {e}", file=sys.stderr)
        error_text = translation_manager.get_text("ai.something_went_wrong", message.author.id, message.guild.id)
        await message.reply(error_text)
        return

    reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()

    reply2 = reply
    if reply:
        reply2 = reply
        for name in sorted(user_map.keys(), key=lambda x: -len(x)):
            pattern = r'<@' + re.escape(name) + r'>'
            replacement = f'<@{user_map[name]}>'+' '
            reply = re.sub(pattern, replacement, reply)
            if reply != reply2:
                break

        print(repr(reply))
        if "\n" in reply:
            thinking_text = translation_manager.get_text("ai.too_much_thinking", message.author.id, message.guild.id)
            reply = thinking_text
            reply2 = thinking_text

        await message.reply(reply)

        history.append({"role": "assistant", "content": reply2})

        save_ai_history(message.guild.id, history)

    training_data_path = os.path.join("data", "training_data.jsonl")
    os.makedirs(os.path.dirname(training_data_path), exist_ok=True)
    with open(training_data_path, "a", encoding="utf-8") as f:
        training_format = translation_manager.get_text("ai.training_format", None, None, 
                                                      author=message.author.name, 
                                                      content=message.content, 
                                                      reply=reply2)
        json.dump({
            "conversation": training_format
        }, f, ensure_ascii=False)
        f.write("\n")

@bot.event
async def on_voice_state_update(member, before, after):
    if bot_locked:
        return
    if member == bot.user and before.channel and not after.channel:
        guild_id = before.channel.guild.id
        if guild_id in music_players:
            player = music_players[guild_id]
            await stop_music(player)

    if before.channel and not after.channel:
        for guild_id, player in music_players.items():
            if (player.voice_client and
                player.voice_client.channel and
                before.channel.id == player.voice_client.channel.id):
                await check_voice_channel_empty(player)

# ==================== ADMIN COMMANDS ====================

@app_commands.guild_only()
@bot.tree.command(name="setaichannel", description=get_command_description("setaichannel"))
@app_commands.checks.has_permissions(administrator=True)
async def setaichannel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await check_changelog_and_module(interaction, "ai"):
        return
    await interaction.response.defer(ephemeral=True)
    channel_set_msg = translation_manager.get_text("ai.channel_set", interaction.user.id, interaction.guild_id, channel_id=channel.id)
    await interaction.followup.send(channel_set_msg, ephemeral=True)

    config = load_ai_channel_config()
    config[str(interaction.guild_id)] = channel.id
    save_ai_channel_config(config)

    guild = interaction.guild
    user_map = {}

    async for member in guild.fetch_members(limit=None):
        if not member.bot:
            user_map[member.name] = str(member.id)

    save_ai_user_map(guild.id, user_map)
    saved_msg = translation_manager.get_text("logging.user_mapping_saved", count=len(user_map), name=guild.name)
    print(saved_msg)
    await log_action(interaction.guild, "SetAIChannel", interaction.user, executor=interaction.user)

@app_commands.guild_only()
@bot.tree.command(name="setchangelogchannel", description=get_command_description("setchangelogchannel"))
@app_commands.describe(channel=get_parameter_description("changelog_channel"))
async def setchangelogchannel(ctx, channel: discord.TextChannel):
    if not ctx.user.guild_permissions.administrator:
        no_perms_msg = translation_manager.get_text("general.no_permissions_short", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_msg,color=discord.Color.red()),ephemeral=True)
    await ctx.response.defer(ephemeral=True)
    cd = load_channel_data("changelog")
    cd[str(ctx.guild.id)] = channel.id
    save_channel_data("changelog",cd)
    
    changelog_set_msg = translation_manager.get_text("moderation.changelog_channel_set", ctx.user.id, ctx.guild.id, channel=channel.mention)
    embed = discord.Embed(description=changelog_set_msg,color=discord.Color.green())
    await ctx.followup.send(embed=embed,ephemeral=True)
    await log_action(ctx.guild,"SetChangelogChannel",ctx.user, executor=ctx.user)
    guild = bot.get_guild(int(ctx.guild.id))
    channel = guild.get_channel(channel.id)
    title_welcome = translation_manager.get_text("welcome.title")
    description_changelog = translation_manager.get_text("welcome.changelog_set")
    footer_welcome = translation_manager.get_text("welcome.footer")
    
    embed = discord.Embed(
        title=title_welcome,
        description=description_changelog,
        color=discord.Color.blurple()
    )
    embed.set_footer(text=footer_welcome)
    await channel.send(embed=embed)
    

@bot.tree.command(name="naptime", description=get_command_description("naptime"))
async def naptime(interaction: discord.Interaction):
    if interaction.user.id not in AUTHORIZED_USERS:
        no_access_msg = translation_manager.get_text("logging.no_access", interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(no_access_msg, ephemeral=True)
        return

    blocking_msg = translation_manager.get_text("bot_management.blocking_channels", interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(blocking_msg, ephemeral=True)

    # Lock AI channels
    ai_config = load_ai_channel_config()
    for guild_id, channel_id in ai_config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    maintenance_msg = translation_manager.get_text("ai.maintenance_block")
                    await channel.send(maintenance_msg)
                    await channel.set_permissions(guild.default_role, send_messages=False)  
                except Exception as e:
                    error_msg = translation_manager.get_text("tempmail.block_channel_error", guild_id=guild_id, error=str(e))
                    print(error_msg)
        bot_locked_per_guild[int(guild_id)] = True
    changelog_channels = load_channel_data("changelog")
    maintenance_title = translation_manager.get_text("bot_management.maintenance_title")
    embed = discord.Embed(
        title=maintenance_title,
        color=discord.Color.yellow(),
        timestamp=datetime.now(timezone.utc)
    )

    for guild_id, channel_id in changelog_channels.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    print(f"Failed to send update info to server {guild_id}: {e}")

    await bot.change_presence(status=discord.Status.invisible)

@bot.tree.command(name="wakeywakey", description=get_command_description("wakeywakey"))
async def wakeywakey(interaction: discord.Interaction):
    if interaction.user.id not in AUTHORIZED_USERS:
        no_access_msg = translation_manager.get_text("logging.no_access", interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(no_access_msg, ephemeral=True)
        return

    unblocking_msg = translation_manager.get_text("bot_management.unblocking_channels", interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(unblocking_msg, ephemeral=True)

    # Unlock AI channels
    ai_config = load_ai_channel_config()
    for guild_id, channel_id in ai_config.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.set_permissions(guild.default_role, send_messages=None)
                    maintenance_complete_msg = translation_manager.get_text("ai.maintenance_unblock")
                    await channel.send(maintenance_complete_msg)
                except Exception as e:
                    error_msg = translation_manager.get_text("tempmail.unblock_channel_error", guild_id=guild_id, error=str(e))
                    print(error_msg)
        bot_locked_per_guild[int(guild_id)] = False
    changelog_channels = load_channel_data("changelog")
    maintenance_completed_title = translation_manager.get_text("bot_management.maintenance_completed")
    embed = discord.Embed(
        title=maintenance_completed_title,
        color=discord.Color.dark_green(),
        timestamp=datetime.now(timezone.utc)
    )
    for guild_id, channel_id in changelog_channels.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    print(f"Failed to send update info to server {guild_id}: {e}")

    await bot.change_presence(status=discord.Status.online)

# Changelog system
AUTHORIZED_USERS = [888076691266736228, 593347933303472128]

def get_version_info():
    """Load version info from file, or return default 1.0"""
    path = "version.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
            return data.get("version", 1), data.get("patch", 0)
    else:
        return 1, 0

def save_version_info(version, patch):
    path = "version.json"
    with open(path, "w") as f:
        json.dump({"version": version, "patch": patch}, f)

def bump_version(ver_type):
    """Returns (new_version, new_patch, version_string) after increment."""
    version, patch = get_version_info()
    if ver_type == "version":
        version += 1
        patch = 0
    else:
        patch += 1
    save_version_info(version, patch)
    return version, patch, f"{version}.{patch}"


@bot.tree.command(name="changelog", description=get_command_description("changelog"))
@app_commands.describe(
    version_type=get_parameter_description("version_type"),
    added=get_parameter_description("added"),
    removed=get_parameter_description("removed"),
    fixed=get_parameter_description("fixed")
)
@app_commands.choices(version_type=[
    app_commands.Choice(name=translation_manager.get_text("changelog.version_choice", None, None), value="version"),
    app_commands.Choice(name=translation_manager.get_text("changelog.patch_choice", None, None), value="patch")
])
async def changelog_cmd(interaction: discord.Interaction, version_type: str, added: str = None, removed: str = None, fixed: str = None):
    if interaction.user.id not in AUTHORIZED_USERS:
        no_perms_msg = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(no_perms_msg, ephemeral=True)

    if not any([added, removed, fixed]):
        must_provide_msg = translation_manager.get_text("general.must_provide_change", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(must_provide_msg, ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    
    version, patch, version_str = bump_version(version_type)

    embed = discord.Embed(
        title=f"📋 Changelog v{version_str}",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )

    if added:
        added_text = translation_manager.get_text("changelog.added_text", interaction.user.id, interaction.guild_id)
        embed.add_field(name=added_text, value=added, inline=False)
    if removed:
        removed_text = translation_manager.get_text("changelog.removed_text", interaction.user.id, interaction.guild_id)
        embed.add_field(name=removed_text, value=removed, inline=False)
    if fixed:
        fixed_text = translation_manager.get_text("changelog.fixed_text", interaction.user.id, interaction.guild_id)
        embed.add_field(name=fixed_text, value=fixed, inline=False)

    embed.set_footer(text=f"Changelog by {interaction.user.display_name}")

    changelog_channels = load_channel_data("changelog")
    sent_count = 0

    for guild_id, channel_id in changelog_channels.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                    sent_count += 1
                except Exception as e:
                    print(f"Failed to send changelog to server {guild_id}: {e}")

    await interaction.followup.send(
        translation_manager.get_text("changelog.sent", interaction.user.id, interaction.guild.id, version=version_str, count=sent_count), ephemeral=True
    )
    await log_action(interaction.guild, "Changelog", interaction.user,
                    reason=f"Type: {version_type}, v{version_str}, Sent to {sent_count} servers",
                    executor=interaction.user)


@bot.tree.command(name="announce", description=get_command_description("announce"))
@app_commands.describe(message=get_parameter_description("message"))
async def announce_cmd(interaction: discord.Interaction, message: str):
    if interaction.user.id not in AUTHORIZED_USERS:
        no_perms_text = translation_manager.get_text("general.no_command_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(no_perms_text, ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    announce_title = translation_manager.get_text("announcements.title", interaction.user.id, interaction.guild.id)
    announce_footer = translation_manager.get_text("announcements.footer", interaction.user.id, interaction.guild.id, user=interaction.user.display_name)
    embed = discord.Embed(
        title=announce_title,
        description=message,
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=announce_footer)

    changelog_channels = load_channel_data("changelog")
    sent_count = 0

    for guild_id, channel_id in changelog_channels.items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                    sent_count += 1
                except Exception as e:
                    print(f"Failed to send announcement to server {guild_id}: {e}")

    announce_sent = translation_manager.get_text("announcements.sent", interaction.user.id, interaction.guild.id, count=sent_count)
    await interaction.followup.send(announce_sent, ephemeral=True)
    await log_action(interaction.guild, "Announcement", interaction.user,
                    reason=f"Sent to {sent_count} servers",
                    executor=interaction.user)

# ==================== MODERATION COMMANDS ====================

@app_commands.guild_only()
@bot.tree.command(name="setperms", description=get_command_description("setperms"))
@app_commands.describe(role=get_parameter_description("role"), permissions=get_parameter_description("permissions"))
async def setperms(ctx, role: discord.Role, permissions: str):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(
            embed=discord.Embed(description=no_perms_text, color=discord.Color.red()),
            ephemeral=True
        )
    await ctx.response.defer(ephemeral=True)
    gid = str(ctx.guild.id)
    cfg = load_config(gid)
    plist = [p.strip().lower() for p in permissions.split(",") if p.strip().lower() in ("warn","ban","kick","timeout")]
    existing = cfg.get("permissions", {}).get(str(role.id), [])
    new_perms = [p for p in plist if p not in existing]
    if not new_perms:
        role_has_perms_text = translation_manager.get_text("moderation.role_already_has_perms", ctx.user.id, ctx.guild.id, role=role.mention)
        return await ctx.followup.send(
            embed=discord.Embed(
                description=role_has_perms_text,
                color=discord.Color.blue()
            ),
            ephemeral=True
        )
    cfg.setdefault("permissions", {}).setdefault(str(role.id), [])
    cfg["permissions"][str(role.id)].extend(new_perms)
    save_config(gid, cfg)
    title_text = translation_manager.get_text("moderation.permissions_updated", ctx.user.id, ctx.guild.id)
    description_text = translation_manager.get_text("moderation.permissions_added", ctx.user.id, ctx.guild.id, role=role.mention, perms=', '.join(new_perms))
    embed = discord.Embed(
        title=title_text,
        description=description_text,
        color=discord.Color.blue()
    )
    await ctx.followup.send(embed=embed, ephemeral=True)
    await log_action(ctx.guild, "SetPerms", ctx.user, reason=f"{role.name}: {new_perms}", executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="revokeperms", description=get_command_description("revokeperms"))
@app_commands.describe(role=get_parameter_description("role"), permissions=get_parameter_description("permissions"))
async def revokeperms(ctx, role: discord.Role, permissions: str):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(
            embed=discord.Embed(description=no_perms_text, color=discord.Color.red()),
            ephemeral=True
        )
    await ctx.response.defer(ephemeral=True)
    gid = str(ctx.guild.id)
    cfg = load_config(gid)
    plist = [p.strip().lower() for p in permissions.split(",") if p.strip().lower() in ("warn","ban","kick","timeout")]
    existing = cfg.get("permissions", {}).get(str(role.id), [])
    to_remove = [p for p in plist if p in existing]
    if not to_remove:
        role_no_perms_text = translation_manager.get_text("moderation.role_no_perms", ctx.user.id, ctx.guild.id, role=role.mention)
        return await ctx.followup.send(
            embed=discord.Embed(
                description=role_no_perms_text,
                color=discord.Color.blue()
            ),
            ephemeral=True
        )
    for p in to_remove:
        cfg["permissions"][str(role.id)].remove(p)
    if not cfg["permissions"][str(role.id)]:
        del cfg["permissions"][str(role.id)]
    save_config(gid, cfg)
    title_text = translation_manager.get_text("moderation.permissions_removed", ctx.user.id, ctx.guild.id)
    description_text = translation_manager.get_text("moderation.permissions_removed_text", ctx.user.id, ctx.guild.id, role=role.mention, perms=', '.join(to_remove))
    embed = discord.Embed(
        title=title_text,
        description=description_text,
        color=discord.Color.blue()
    )
    await ctx.followup.send(embed=embed, ephemeral=True)
    await log_action(ctx.guild, "RevokePerms", ctx.user, reason=f"{role.name}: {to_remove}", executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="permslist", description=get_command_description("permslist"))
@app_commands.default_permissions(administrator=True)
async def permslist(ctx):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    await ctx.response.defer(ephemeral=True)
    gid = str(ctx.guild.id)
    cfg = load_config(gid)
    title_text = translation_manager.get_text("moderation.permissions_list_title", ctx.user.id, ctx.guild.id)
    embed = discord.Embed(title=title_text, color=discord.Color.blue())

    if not cfg.get("permissions"):
        no_perms_set_text = translation_manager.get_text("moderation.no_permissions_set", ctx.user.id, ctx.guild.id)
        embed.description = no_perms_set_text
    else:
        for rid, perms in cfg["permissions"].items():
            role = ctx.guild.get_role(int(rid))
            if role:
                permissions_text = translation_manager.get_text("moderation.role_permissions", ctx.user.id, ctx.guild.id, perms=', '.join(perms))
                embed.add_field(
                    name=f"{role.name}",
                    value=f"{role.mention}\n{permissions_text}",
                    inline=False
                )
            else:
                permissions_text = translation_manager.get_text("moderation.role_permissions", ctx.user.id, ctx.guild.id, perms=', '.join(perms))
                embed.add_field(
                    name=f"ID roli: {rid}",
                    value=f"`{rid}`\n{permissions_text}",
                    inline=False
                )

    await ctx.followup.send(embed=embed, ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="warn", description=get_command_description("warn"))
@app_commands.describe(user=get_parameter_description("user"), reason=get_parameter_description("reason"))
async def warn(ctx, user: discord.Member, reason: str):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not has_permission(ctx,"warn"):
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    gid,uid=str(ctx.guild.id),str(user.id)
    warns=load_user_warns(gid,uid)
    warns.append({"reason":reason,"timestamp":datetime.now().strftime('%Y-%m-%d %H:%M:%S'),"by":ctx.user.id})
    save_user_warns(gid,uid,warns)
    title_text = translation_manager.get_text("moderation.user_warned", ctx.user.id, ctx.guild.id)
    description_text = translation_manager.get_text("moderation.warning_number", ctx.user.id, ctx.guild.id, user=user.mention, count=len(warns))
    embed=discord.Embed(title=title_text, description=description_text, color=discord.Color.orange())
    reason_text = translation_manager.get_text("general.reason", ctx.user.id, ctx.guild.id)
    embed.add_field(name=reason_text,value=reason,inline=False)
    await ctx.followup.send(embed=embed)
    await log_action(ctx.guild,"Warning",user,reason, executor=ctx.user)
    count=len(warns)
    if count==2:
        reason_2 = translation_manager.get_text("actions.warn_reason_2", ctx.user.id, ctx.guild.id)
        await user.timeout(timedelta(minutes=10),reason=reason_2)
        mute_text = translation_manager.get_text("moderation.user_muted_10min", ctx.user.id, ctx.guild.id, user=user.mention)
        await ctx.followup.send(embed=discord.Embed(description=mute_text, color=discord.Color.orange()))
        timeout_10m = translation_manager.get_text("actions.timeout_10m", ctx.user.id, ctx.guild.id)
        await log_action(ctx.guild,"Timeout",user,reason_2,timeout_10m, executor=bot.user)
    elif count==3:
        reason_3 = translation_manager.get_text("actions.warn_reason_3", ctx.user.id, ctx.guild.id)
        timeout_1h = translation_manager.get_text("actions.timeout_1h", ctx.user.id, ctx.guild.id)
        await user.timeout(timedelta(hours=1),reason=reason_3)
        mute_text = translation_manager.get_text("moderation.user_muted_1hour", ctx.user.id, ctx.guild.id, user=user.mention)
        await ctx.followup.send(embed=discord.Embed(description=mute_text, color=discord.Color.orange()))
        await log_action(ctx.guild,"Timeout",user,reason_3,timeout_1h, executor=bot.user)
    elif count==4:
        reason_4 = translation_manager.get_text("actions.warn_reason_4", ctx.user.id, ctx.guild.id)
        ban_1d = translation_manager.get_text("actions.ban_1d", ctx.user.id, ctx.guild.id)
        await user.ban(reason=reason_4,delete_message_days=0)
        ban_text = translation_manager.get_text("moderation.user_banned_1day", ctx.user.id, ctx.guild.id, user=user.mention)
        await ctx.followup.send(embed=discord.Embed(description=ban_text, color=discord.Color.orange()))
        await log_action(ctx.guild,"Ban",user,reason_4,ban_1d, executor=bot.user)
        await asyncio.sleep(86400)
        end_ban_reason = translation_manager.get_text("moderation.ban_ended", None, ctx.guild.id)
        await ctx.guild.unban(user,reason=end_ban_reason)
        await log_action(ctx.guild,"Unban",user,end_ban_reason, executor=bot.user)
    elif count>=5:
        reason_5 = translation_manager.get_text("actions.warn_reason_5", ctx.user.id, ctx.guild.id)
        await user.ban(reason=reason_5,delete_message_days=0)
        ban_text = translation_manager.get_text("moderation.user_banned_permanent", ctx.user.id, ctx.guild.id, user=user.mention)
        await ctx.followup.send(embed=discord.Embed(description=ban_text, color=discord.Color.red()))
        await log_action(ctx.guild,"Ban",user,reason_5, executor=bot.user)

@app_commands.guild_only()
@bot.tree.command(name="warns", description=get_command_description("warns"))
@app_commands.describe(user=get_parameter_description("user_optional"))
async def warns(ctx, user: discord.Member = None):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if user is None:
        user = ctx.user
    elif user.id != ctx.user.id and not (has_permission(ctx, "warn") or ctx.user.guild_permissions.administrator):
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer(ephemeral=(user.id == ctx.user.id))
    gid, uid = str(ctx.guild.id), str(user.id)
    warns = load_user_warns(gid, uid)
    if not warns:
        no_warnings_text = translation_manager.get_text("moderation.user_no_warnings", ctx.user.id, ctx.guild.id, user=user.mention)
        return await ctx.followup.send(embed=discord.Embed(description=no_warnings_text, color=discord.Color.blue()))
    title_text = translation_manager.get_text("moderation.warnings_title", ctx.user.id, ctx.guild.id, username=user.name)
    description_text = translation_manager.get_text("moderation.warnings_count", ctx.user.id, ctx.guild.id, count=len(warns))
    embed = discord.Embed(title=title_text, description=description_text, color=discord.Color.orange())
    for i, w in enumerate(warns, 1):
        warner = ctx.guild.get_member(w["by"])
        wname = warner.name if warner else translation_manager.get_text("moderation.unknown_warner", ctx.user.id, ctx.guild.id)
        warning_title = translation_manager.get_text("moderation.warning_entry", ctx.user.id, ctx.guild.id, number=i, timestamp=w["timestamp"])
        warning_details = translation_manager.get_text("moderation.warning_details", ctx.user.id, ctx.guild.id, reason=w["reason"], warner=wname)
        embed.add_field(name=warning_title, value=warning_details, inline=False)
    await ctx.followup.send(embed=embed)

# Continue merging remaining commands and features from both bots and requested new features here...

@app_commands.guild_only()
@bot.tree.command(name="clearwarns", description=get_command_description("clearwarns"))
@app_commands.describe(user=get_parameter_description("user"))
async def clearwarns(ctx, user: discord.Member):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    gid,uid=str(ctx.guild.id),str(user.id)
    path=get_warns_path(gid,uid)
    if os.path.exists(path):
        os.remove(path)
        warnings_cleared_text = translation_manager.get_text("moderation.warnings_cleared_user", ctx.user.id, ctx.guild.id, user=user.mention)
        await ctx.followup.send(embed=discord.Embed(description=warnings_cleared_text, color=discord.Color.green()))
        await log_action(ctx.guild,"ClearWarnings",user, executor=ctx.user)
    else:
        no_warnings_text = translation_manager.get_text("general.no_warnings_to_remove", ctx.user.id, ctx.guild.id)
        await ctx.followup.send(embed=discord.Embed(description=no_warnings_text, color=discord.Color.blue()))

@app_commands.guild_only()
@bot.tree.command(name="ban", description=get_command_description("ban"))
@app_commands.describe(user=get_parameter_description("user"), reason=get_parameter_description("reason"), time=get_parameter_description("time"))
async def ban(ctx, user: discord.Member, reason: str = None, time: str = None):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not has_permission(ctx,"ban"):
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    duration = None
    temp = False
    if time:
        temp = True
        if time.endswith('d'):
            try: duration=timedelta(days=int(time[:-1]))
            except: 
                invalid_time_text = translation_manager.get_text("general.invalid_time", ctx.user.id, ctx.guild.id)
                return await ctx.followup.send(embed=discord.Embed(description=invalid_time_text, color=discord.Color.red()))
        elif time.endswith('h'):
            try: duration=timedelta(hours=int(time[:-1]))
            except: 
                invalid_time_text = translation_manager.get_text("general.invalid_time", ctx.user.id, ctx.guild.id)
                return await ctx.followup.send(embed=discord.Embed(description=invalid_time_text, color=discord.Color.red()))
        else:
            invalid_format_text = translation_manager.get_text("general.invalid_format", ctx.user.id, ctx.guild.id)
            return await ctx.followup.send(embed=discord.Embed(description=invalid_format_text, color=discord.Color.red()))
    await user.ban(reason=reason or "")
    text = translation_manager.get_text("moderation.user_banned_text", ctx.user.id, ctx.guild.id, user=user.mention)
    if reason: 
        reason_text = translation_manager.get_text("general.reason", ctx.user.id, ctx.guild.id)
        text += f" {reason_text}: {reason}"
    if temp: 
        time_text = translation_manager.get_text("general.duration", ctx.user.id, ctx.guild.id)
        text += f"\n{time_text}: {time}"
    embed=discord.Embed(description=text,color=discord.Color.orange())
    await ctx.followup.send(embed=embed)
    await log_action(ctx.guild,"Ban",user,reason, time if temp else None, executor=ctx.user)
    if temp:
        await asyncio.sleep(duration.total_seconds())
        end_ban_reason = translation_manager.get_text("moderation.ban_ended", None, ctx.guild.id)
        await ctx.guild.unban(user,reason=end_ban_reason)
        await log_action(ctx.guild,"Unban",user,end_ban_reason, executor=bot.user)

@app_commands.guild_only()
@bot.tree.command(name="kick", description=get_command_description("kick"))
@app_commands.describe(user=get_parameter_description("user"), reason=get_parameter_description("reason"))
async def kick(ctx, user: discord.Member, reason: str = None):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not has_permission(ctx,"kick"):
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    await user.kick(reason=reason or "")
    text = translation_manager.get_text("moderation.user_kicked_text", ctx.user.id, ctx.guild.id, user=user.mention)
    if reason: 
        reason_text = translation_manager.get_text("general.reason", ctx.user.id, ctx.guild.id)
        text += f" {reason_text}: {reason}"
    embed=discord.Embed(description=text,color=discord.Color.orange())
    await ctx.followup.send(embed=embed)
    await log_action(ctx.guild,"Kick",user,reason, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="timeout", description=get_command_description("timeout"))
@app_commands.describe(user=get_parameter_description("user"), time=get_parameter_description("time_long"), reason=get_parameter_description("reason"))
async def timeout(ctx, user: discord.Member, time: str, reason: str = None):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not has_permission(ctx,"timeout"):
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    dur = None
    if time.endswith('d'):
        try: dur=timedelta(days=int(time[:-1]))
        except: 
            invalid_time_text = translation_manager.get_text("general.invalid_time", ctx.user.id, ctx.guild.id)
            return await ctx.followup.send(embed=discord.Embed(description=invalid_time_text, color=discord.Color.red()))
    elif time.endswith('h'):
        try: dur=timedelta(hours=int(time[:-1]))
        except: 
            invalid_time_text = translation_manager.get_text("general.invalid_time", ctx.user.id, ctx.guild.id)
            return await ctx.followup.send(embed=discord.Embed(description=invalid_time_text, color=discord.Color.red()))
    elif time.endswith('m'):
        try: dur=timedelta(minutes=int(time[:-1]))
        except: 
            invalid_time_text = translation_manager.get_text("general.invalid_time", ctx.user.id, ctx.guild.id)
            return await ctx.followup.send(embed=discord.Embed(description=invalid_time_text, color=discord.Color.red()))
    else:
        invalid_format_text = translation_manager.get_text("general.invalid_format", ctx.user.id, ctx.guild.id)
        return await ctx.followup.send(embed=discord.Embed(description=invalid_format_text, color=discord.Color.red()))
    await user.timeout(dur,reason=reason or "")
    text = translation_manager.get_text("moderation.user_muted_text", ctx.user.id, ctx.guild.id, user=user.mention, time=time)
    if reason: 
        reason_text = translation_manager.get_text("general.reason", ctx.user.id, ctx.guild.id)
        text += f" {reason_text}: {reason}"
    embed=discord.Embed(description=text,color=discord.Color.orange())
    await ctx.followup.send(embed=embed)
    await log_action(ctx.guild,"Timeout",user,reason,time, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="unban", description=get_command_description("unban"))
@app_commands.describe(user_id=get_parameter_description("user_id"))
async def unban(ctx, user_id: str):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not has_permission(ctx,"ban"):
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    try:
        uid=int(user_id)
        user=await bot.fetch_user(uid)
        await ctx.guild.unban(user)
        unbanned_text = translation_manager.get_text("moderation.user_unbanned_text", ctx.user.id, ctx.guild.id, user=user.name)
        embed=discord.Embed(description=unbanned_text, color=discord.Color.green())
        await ctx.followup.send(embed=embed)
        await log_action(ctx.guild,"Unban",user, executor=ctx.user)
    except:
        error_text = translation_manager.get_text("general.unban_error", ctx.user.id, ctx.guild.id)
        await ctx.followup.send(embed=discord.Embed(description=error_text, color=discord.Color.red()))

@app_commands.guild_only()
@bot.tree.command(name="setlogchannel", description=get_command_description("setlogchannel"))
@app_commands.describe(channel=get_parameter_description("log_channel"))
async def setlogchannel(ctx, channel: discord.TextChannel):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer(ephemeral=True)
    cd = load_channel_data("loggers")
    cd[str(ctx.guild.id)] = channel.id
    save_channel_data("loggers",cd)
    log_channel_text = translation_manager.get_text("moderation.log_channel_set", ctx.user.id, ctx.guild.id, channel=channel.mention)
    embed = discord.Embed(description=log_channel_text, color=discord.Color.green())
    await ctx.followup.send(embed=embed,ephemeral=True)
    await log_action(ctx.guild,"SetLogChannel",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="setwelcomechannel", description=get_command_description("setwelcomechannel"))
@app_commands.describe(channel=get_parameter_description("welcome_channel"))
async def setwelcomechannel(ctx, channel: discord.TextChannel):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer(ephemeral=True)
    cd = load_channel_data("welcomers")
    cd[str(ctx.guild.id)] = channel.id
    save_channel_data("welcomers",cd)
    welcome_channel_text = translation_manager.get_text("moderation.welcome_channel_set", ctx.user.id, ctx.guild.id, channel=channel.mention)
    embed = discord.Embed(description=welcome_channel_text, color=discord.Color.green())
    await ctx.followup.send(embed=embed,ephemeral=True)
    await log_action(ctx.guild,"SetWelcomeChannel",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="setcounterchannel", description=get_command_description("setcounterchannel"))
@app_commands.describe(channel=get_parameter_description("counting_channel"))
async def setcounterchannel(ctx, channel: discord.TextChannel):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer(ephemeral=True)
    gid = str(ctx.guild.id)
    cd = load_channel_data("counters")
    cd[gid] = channel.id
    save_channel_data("counters",cd)
    save_counter_data(gid,{"last_number":1,"last_user":None})
    await channel.send(translation_manager.get_text("counter.first_number", interaction.user.id, interaction.guild_id))
    counter_channel_text = translation_manager.get_text("moderation.counter_channel_set", ctx.user.id, ctx.guild.id, channel=channel.mention)
    embed = discord.Embed(description=counter_channel_text, color=discord.Color.green())
    await ctx.followup.send(embed=embed,ephemeral=True)
    await log_action(ctx.guild,"SetCounterChannel",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="setpingchannel", description=get_command_description("setpingchannel"))
@app_commands.describe(channel=get_parameter_description("ping_channel"))
async def setpingchannel(ctx, channel: discord.TextChannel):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer(ephemeral=True)
    cd = load_channel_data("pingers")
    cd[str(ctx.guild.id)] = channel.id
    save_channel_data("pingers",cd)
    ping_channel_text = translation_manager.get_text("moderation.ping_channel_set", ctx.user.id, ctx.guild.id, channel=channel.mention)
    embed = discord.Embed(description=ping_channel_text, color=discord.Color.green())
    await ctx.followup.send(embed=embed,ephemeral=True)
    await log_action(ctx.guild,"SetPingChannel",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="lockdown", description=get_command_description("lockdown"))
async def lockdown(ctx):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    locked_text = translation_manager.get_text("moderation.channel_locked_text", ctx.user.id, ctx.guild.id, channel=ctx.channel.mention)
    embed = discord.Embed(description=locked_text, color=discord.Color.orange())
    await ctx.followup.send(embed=embed)
    await log_action(ctx.guild,"Lockdown",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="unlock", description=get_command_description("unlock"))
async def unlock(ctx):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(embed=discord.Embed(description=no_perms_text, color=discord.Color.red()), ephemeral=True)
    await ctx.response.defer()
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
    unlocked_text = translation_manager.get_text("moderation.channel_unlocked_text", ctx.user.id, ctx.guild.id, channel=ctx.channel.mention)
    embed = discord.Embed(description=unlocked_text, color=discord.Color.green())
    await ctx.followup.send(embed=embed)
    await log_action(ctx.guild,"Unlock",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="purge", description=get_command_description("purge"))
@app_commands.describe(amount=get_parameter_description("amount_1_100"))
async def purge(ctx, amount: int):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    if not ctx.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", ctx.user.id, ctx.guild.id)
        return await ctx.response.send_message(
            embed=discord.Embed(description=no_perms_text, color=discord.Color.red()),
            ephemeral=True
        )
    await ctx.response.defer(ephemeral=True)
    if amount < 1 or amount > 100:
        amount_error_text = translation_manager.get_text("general.amount_1_100", ctx.user.id, ctx.guild.id)
        return await ctx.followup.send(
            embed=discord.Embed(description=amount_error_text, color=discord.Color.red()),
            ephemeral=True
        )
    try:
        deleted = await ctx.channel.purge(limit=amount)
        deleted_text = translation_manager.get_text("moderation.messages_deleted", ctx.user.id, ctx.guild.id, count=len(deleted))
        embed = discord.Embed(
            description=deleted_text,
            color=discord.Color.green()
        )
        await ctx.followup.send(embed=embed)
        purge_reason = translation_manager.get_text("actions.purge_log", ctx.user.id, ctx.guild.id, count=len(deleted), channel=ctx.channel.name)
        await log_action(ctx.guild, "Purge", ctx.user, reason=purge_reason, executor=ctx.user)
    except Exception as e:
        await ctx.followup.send(
            embed=discord.Embed(
                title=translation_manager.get_text("errors.purge_error", ctx.user.id, ctx.guild.id),
                description=f"{type(e).__name__}: {e}",
                color=discord.Color.red()
            ),
            ephemeral=True
        )

@app_commands.guild_only()
@bot.tree.command(name="clearchannels", description=get_command_description("clearchannels"))
@app_commands.default_permissions(administrator=True)
async def clearchannels(ctx):
    if not await check_changelog_and_module(ctx, "moderation"):
        return
    await ctx.response.defer(ephemeral=True)
    gid=str(ctx.guild.id)
    for f in ("counters","loggers","welcomers", "pingers", "econlogs", "changelog"):
        cd=load_channel_data(f)
        if gid in cd:
            del cd[gid]
            save_channel_data(f,cd)
    channels_cleared_text = translation_manager.get_text("moderation.channels_cleared", ctx.user.id, ctx.guild.id)
    await ctx.followup.send(embed=discord.Embed(description=channels_cleared_text, color=discord.Color.green()), ephemeral=True)
    await log_action(ctx.guild,"ClearChannels",ctx.user, executor=ctx.user)

@app_commands.guild_only()
@bot.tree.command(name="showchannels", description=get_command_description("showchannels"))
@app_commands.default_permissions(administrator=True)
async def showchannels(ctx):
    await ctx.response.defer(ephemeral=True)
    gid = str(ctx.guild.id)

    # Get translated labels
    changelog_label = translation_manager.get_text("channels.changelog", ctx.user.id, ctx.guild.id)
    logs_label = translation_manager.get_text("channels.logs", ctx.user.id, ctx.guild.id)
    welcome_label = translation_manager.get_text("channels.welcome", ctx.user.id, ctx.guild.id)
    counting_label = translation_manager.get_text("channels.counting", ctx.user.id, ctx.guild.id)
    ping_label = translation_manager.get_text("channels.ping", ctx.user.id, ctx.guild.id)
    econlogs_label = translation_manager.get_text("channels.economy_logs", ctx.user.id, ctx.guild.id)
    
    cfg = {
        changelog_label: load_channel_data("changelog").get(gid),
        logs_label:    load_channel_data("loggers").get(gid),
        welcome_label:  load_channel_data("welcomers").get(gid),
        counting_label: load_channel_data("counters").get(gid),
        ping_label: load_channel_data("pingers").get(gid),
        econlogs_label: load_channel_data("econlogs").get(gid)
    }

    title_text = translation_manager.get_text("channels.title", ctx.user.id, ctx.guild.id)
    embed = discord.Embed(title=title_text, color=discord.Color.blue())
    for label, ch_id in cfg.items():
        if ch_id:
            channel = ctx.guild.get_channel(int(ch_id))
            unknown_text = translation_manager.get_text("channels.unknown", ctx.user.id, ctx.guild.id)
            mention = channel.mention if channel else unknown_text
        else:
            none_text = translation_manager.get_text("channels.none", ctx.user.id, ctx.guild.id)
            mention = none_text
        embed.add_field(name=label, value=mention, inline=False)

    await ctx.followup.send(embed=embed, ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="help", description=get_command_description("help"))
async def help(ctx):
    changelog_channels = load_channel_data("changelog")
    if not changelog_channels.get(str(ctx.guild.id)):
        msg = translation_manager.get_text("logging.changelog_not_set", ctx.user.id, ctx.guild.id)
        await ctx.response.send_message(msg, ephemeral=True)
        return 
    await ctx.response.defer(ephemeral=True)
    
    # Get translations
    user_id = ctx.user.id
    guild_id = ctx.guild.id

    # --- ADMIN ONLY ---
    admin_cmds = [
        ("/setchangelogchannel", translation_manager.get_text("help.setchangelogchannel", user_id, guild_id)),
        ("/setaichannel", translation_manager.get_text("help.setaichannel", user_id, guild_id)),
        ("/setperms", translation_manager.get_text("help.setperms", user_id, guild_id)),
        ("/revokeperms", translation_manager.get_text("help.revokeperms", user_id, guild_id)),
        ("/permslist", translation_manager.get_text("help.permslist", user_id, guild_id)),
        ("/setlogchannel", translation_manager.get_text("help.setlogchannel", user_id, guild_id)),
        ("/setwelcomechannel", translation_manager.get_text("help.setwelcomechannel", user_id, guild_id)),
        ("/setcounterchannel", translation_manager.get_text("help.setcounterchannel", user_id, guild_id)),
        ("/setpingchannel", translation_manager.get_text("help.setpingchannel", user_id, guild_id)),
        ("/showchannels", translation_manager.get_text("help.showchannels", user_id, guild_id)),
        ("/clearchannels", translation_manager.get_text("help.clearchannels", user_id, guild_id)),
        ("/purge", translation_manager.get_text("help.purge", user_id, guild_id)),
        ("/lockdown", translation_manager.get_text("help.lockdown", user_id, guild_id)),
        ("/unlock", translation_manager.get_text("help.unlock", user_id, guild_id)),
        ("/admin_add", translation_manager.get_text("help.admin_add", user_id, guild_id)),
        ("/admin_remove", translation_manager.get_text("help.admin_remove", user_id, guild_id)),
        ("/admin_setincome", translation_manager.get_text("help.admin_setincome", user_id, guild_id)),
        ("/admin_listincome", translation_manager.get_text("help.admin_listincome", user_id, guild_id)),
        ("/admin_removeincome", translation_manager.get_text("help.admin_removeincome", user_id, guild_id)),
        ("/admin_shopadd", translation_manager.get_text("help.admin_shopadd", user_id, guild_id)),
        ("/admin_shopremove", translation_manager.get_text("help.admin_shopremove", user_id, guild_id)),
        ("/admin_shoplist", translation_manager.get_text("help.admin_shoplist", user_id, guild_id)),
        ("/setwork", translation_manager.get_text("help.setwork", user_id, guild_id)),
        ("/resetecon", translation_manager.get_text("help.resetecon", user_id, guild_id)),
        ("/rrcreate", translation_manager.get_text("help.rrcreate", user_id, guild_id)),
        ("/rrlist", translation_manager.get_text("help.rrlist", user_id, guild_id)),
        ("/rrdelete", translation_manager.get_text("help.rrdelete", user_id, guild_id)),
    ]

    # --- MODERATOR ONLY ---
    mod_cmds = [
        ("/warn", translation_manager.get_text("help.warn", user_id, guild_id)),
        ("/warns", translation_manager.get_text("help.warns", user_id, guild_id)),
        ("/clearwarns", translation_manager.get_text("help.clearwarns", user_id, guild_id)),
        ("/ban", translation_manager.get_text("help.ban", user_id, guild_id)),
        ("/kick", translation_manager.get_text("help.kick", user_id, guild_id)),
        ("/timeout", translation_manager.get_text("help.timeout", user_id, guild_id)),
        ("/unban", translation_manager.get_text("help.unban", user_id, guild_id)),
    ]

    # --- MUZYKA ---
    music_cmds = [
        ("/play", translation_manager.get_text("help.play", user_id, guild_id)),
        ("/queue", translation_manager.get_text("help.queue", user_id, guild_id)),
        ("/skip", translation_manager.get_text("help.skip", user_id, guild_id)),
        ("/stop", translation_manager.get_text("help.stop", user_id, guild_id)),
    ]

    # --- EKONOMIA / HAZARD ---
    econ_cmds = [
        ("/balance", translation_manager.get_text("help.balance", user_id, guild_id)),
        ("/deposit", translation_manager.get_text("help.deposit", user_id, guild_id)),
        ("/withdraw", translation_manager.get_text("help.withdraw", user_id, guild_id)),
        ("/collect", translation_manager.get_text("help.collect", user_id, guild_id)),
        ("/work", translation_manager.get_text("help.work", user_id, guild_id)),
        ("/steal", translation_manager.get_text("help.steal", user_id, guild_id)),
        ("/check", translation_manager.get_text("help.check", user_id, guild_id)),
        ("/baltop", translation_manager.get_text("help.baltop", user_id, guild_id)),
        ("/shop", translation_manager.get_text("help.shop", user_id, guild_id)),
        ("/buy", translation_manager.get_text("help.buy", user_id, guild_id)),
    ]
    hazard_cmds = [
        ("/rps", translation_manager.get_text("help.rps", user_id, guild_id)),
        ("/cf", translation_manager.get_text("help.cf", user_id, guild_id)),
        ("/roulette", translation_manager.get_text("help.roulette", user_id, guild_id)),
        ("/blackjack", translation_manager.get_text("help.blackjack", user_id, guild_id)),
        ("/mines", translation_manager.get_text("help.mines", user_id, guild_id)),
    ]

    other_cmds = [
        ("/help", translation_manager.get_text("help.help", user_id, guild_id)),
        ("/modules", translation_manager.get_text("help.modules", user_id, guild_id)),
        ("/faq", translation_manager.get_text("help.faq", user_id, guild_id)),
        ("/shortenlink", translation_manager.get_text("help.shortenlink", user_id, guild_id)),
        ("/extendlink", translation_manager.get_text("help.extendlink", user_id, guild_id)),
        ("/deletelink", translation_manager.get_text("help.deletelink", user_id, guild_id)),
        ("/mylinks", translation_manager.get_text("help.mylinks", user_id, guild_id)),
        ("/tempmail", translation_manager.get_text("help.tempmail", user_id, guild_id)),
        ("/resetmail", translation_manager.get_text("help.resetmail", user_id, guild_id)),

    ]
    title = translation_manager.get_text("help.title", user_id, guild_id)
    embed = discord.Embed(title=title, color=discord.Color.light_grey())

    # Tylko admin widzi admin
    if ctx.user.guild_permissions.administrator:
        warning_name = translation_manager.get_text("help.warning", user_id, guild_id)
        warning_text = translation_manager.get_text("help.warning_text", user_id, guild_id)
        embed.add_field(name=warning_name, value=warning_text, inline=False)
        
        # Split admin commands into two parts to fit within 1024 character limit
        admin_part1 = admin_cmds[:14]  # First 14 commands
        admin_part2 = admin_cmds[14:]  # Remaining commands
        
        admin_text1 = "\n".join(f"{cmd} — {desc}" for cmd, desc in admin_part1)
        admin_text2 = "\n".join(f"{cmd} — {desc}" for cmd, desc in admin_part2)
        
        admin_name_1 = translation_manager.get_text("help.admin_commands_1", user_id, guild_id)
        admin_name_2 = translation_manager.get_text("help.admin_commands_2", user_id, guild_id)
        embed.add_field(name=admin_name_1, value=admin_text1, inline=False)
        embed.add_field(name=admin_name_2, value=admin_text2, inline=False)
        

    if module_manager.is_module_enabled(ctx.guild.id, "moderation"):
        if has_permission(ctx,"warn") or has_permission(ctx,"ban") or has_permission(ctx,"kick") or has_permission(ctx,"timeout") or ctx.user.guild_permissions.administrator:
            mod_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in mod_cmds)
            mod_name = translation_manager.get_text("help.mod_commands", user_id, guild_id)
            embed.add_field(name=mod_name, value=mod_text, inline=False)
    else:
        if has_permission(ctx,"warn") or has_permission(ctx,"ban") or has_permission(ctx,"kick") or has_permission(ctx,"timeout") or ctx.user.guild_permissions.administrator:
            mod_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in mod_cmds)
            mod_name_disabled = translation_manager.get_text("help.mod_commands_disabled", user_id, guild_id)
            embed.add_field(name=mod_name_disabled, value=mod_text, inline=False)


    if module_manager.is_module_enabled(ctx.guild.id, "music"):
        music_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in music_cmds)
        music_name = translation_manager.get_text("help.music_commands", user_id, guild_id)
        embed.add_field(name=music_name, value=music_text, inline=False)
    else:
        if ctx.user.guild_permissions.administrator:
            music_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in music_cmds)
            music_name_disabled = translation_manager.get_text("help.music_commands_disabled", user_id, guild_id)
            embed.add_field(name=music_name_disabled, value=music_text, inline=False)

    if module_manager.is_module_enabled(ctx.guild.id, "economy"):
        econ_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in econ_cmds)
        econ_name = translation_manager.get_text("help.economy_commands", user_id, guild_id)
        embed.add_field(name=econ_name, value=econ_text, inline=False)
    else:
        if ctx.user.guild_permissions.administrator:
            econ_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in econ_cmds)
            econ_name_disabled = translation_manager.get_text("help.economy_commands_disabled", user_id, guild_id)
            embed.add_field(name=econ_name_disabled, value=econ_text, inline=False)

        
    if module_manager.is_module_enabled(ctx.guild.id, "economy"):
        hazard_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in hazard_cmds)
        hazard_name = translation_manager.get_text("help.gambling_commands", user_id, guild_id)
        embed.add_field(name=hazard_name, value=hazard_text, inline=False)
    else:
        if ctx.user.guild_permissions.administrator:
            hazard_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in hazard_cmds)
            hazard_name_disabled = translation_manager.get_text("help.gambling_commands_disabled", user_id, guild_id)
            embed.add_field(name=hazard_name_disabled, value=hazard_text, inline=False)


    other_text = "\n".join(f"{cmd} — {desc}" for cmd, desc in other_cmds)
    other_name = translation_manager.get_text("help.other_commands", user_id, guild_id)
    embed.add_field(name=other_name, value=other_text, inline=False)

    if ctx.user.guild_permissions.administrator:
        footer_text = translation_manager.get_text("help.footer_admin", user_id, guild_id)
        embed.set_footer(text=footer_text)

    await ctx.followup.send(embed=embed, ephemeral=True)

# ==================== MUSIC COMMANDS ====================

async def create_control_embed(player):
    """Tworzy embed z kontrolkami muzycznymi"""
    player_title = translation_manager.get_text("music.player_title", None, player.guild_id)
    embed = discord.Embed(title=player_title, color=discord.Color.blue())
    
    if player.current_track:
        current = player.current_track
        elapsed_time = (datetime.now() - current.get('start_time', datetime.now())).total_seconds()
        remaining_time = max(current.get('duration', 0) - elapsed_time, 0)
        
        minutes, seconds = divmod(int(remaining_time), 60)
        time_str = f"{minutes}:{seconds:02d}"
        
        now_playing_text = translation_manager.get_text("music.now_playing", None, player.guild_id)
        added_by_text = translation_manager.get_text("music.added_by", None, player.guild_id, user=current['requester'].mention)
        remaining_text = translation_manager.get_text("music.remaining_time", None, player.guild_id, time=time_str)
        embed.add_field(
            name=now_playing_text,
            value=f"**[{current['title']}]({current.get('webpage_url', '')})**\n"
                  f"{added_by_text}\n"
                  f"{remaining_text}",
            inline=False
        )
    else:
        now_playing_text = translation_manager.get_text("music.now_playing", None, player.guild_id)
        nothing_playing_text = translation_manager.get_text("music.nothing_playing", None, player.guild_id)
        embed.add_field(name=now_playing_text, value=nothing_playing_text, inline=False)
    
    next_track = player.get_next_track()
    next_track_text = translation_manager.get_text("music.next_track", None, player.guild_id)
    if next_track:
        added_by_text = translation_manager.get_text("music.added_by", None, player.guild_id, user=next_track['requester'].mention)
        embed.add_field(
            name=next_track_text,
            value=f"**{next_track['title']}**\n{added_by_text}",
            inline=False
        )
    else:
        no_next_text = translation_manager.get_text("music.no_next_track", None, player.guild_id)
        embed.add_field(name=next_track_text, value=no_next_text, inline=False)
    
    volume_text = translation_manager.get_text("music.volume_label", None, player.guild_id)
    queue_text = translation_manager.get_text("music.queue_label", None, player.guild_id)
    queue_count_text = translation_manager.get_text("music.queue_count", None, player.guild_id, count=len(player.queue))
    embed.add_field(name=volume_text, value=f"{int(player.volume * 100)}%", inline=True)
    embed.add_field(name=queue_text, value=queue_count_text, inline=True)
    
    return embed

class MusicControlView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player
    
    def check_permissions(self, interaction):
        """Check if user can use controls"""
        if interaction.user.guild_permissions.administrator:
            return True
        
        if (interaction.user.voice and 
            self.player.voice_client and 
            interaction.user.voice.channel == self.player.voice_client.channel):
            return True
        
        return False
    
    async def send_permission_error(self, interaction):
        """Wysyła wiadomość o błędzie uprawnień"""
        must_be_in_voice_or_admin_text = translation_manager.get_text("music.must_be_in_voice_or_admin", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(must_be_in_voice_or_admin_text, ephemeral=True)
    
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.primary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        if self.player.voice_client and self.player.voice_client.is_playing():
            self.player.voice_client.stop()
            track_skipped_text = translation_manager.get_text("music.track_skipped", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(track_skipped_text, ephemeral=True)
        else:
            nothing_playing_text = translation_manager.get_text("music.nothing_playing", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(nothing_playing_text, ephemeral=True)
    
    @discord.ui.button(label="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        if self.player.voice_client and self.player.voice_client.is_playing():
            self.player.voice_client.pause()
            self.player.is_paused = True
            paused_text = translation_manager.get_text("music.playback_paused", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(paused_text, ephemeral=True)
        else:
            nothing_playing_text = translation_manager.get_text("music.nothing_playing", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(nothing_playing_text, ephemeral=True)
    
    @discord.ui.button(label="▶️", style=discord.ButtonStyle.success, row=0)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        if self.player.voice_client and self.player.voice_client.is_paused():
            self.player.voice_client.resume()
            self.player.is_paused = False
            resumed_text = translation_manager.get_text("music.playback_resumed", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(resumed_text, ephemeral=True)
        else:
            not_paused_text = translation_manager.get_text("music.playback_not_paused", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(not_paused_text, ephemeral=True)
    
    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        await stop_music(self.player)
        stopped_text = translation_manager.get_text("music.playback_stopped", interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(stopped_text, ephemeral=True)
    
    @discord.ui.button(label="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        if self.player.volume < 1.0:
            self.player.volume = min(1.0, self.player.volume + 0.1)
            if self.player.voice_client and hasattr(self.player.voice_client.source, 'volume'):
                self.player.voice_client.source.volume = self.player.volume
            
            await update_control_message(self.player)
            volume_text = translation_manager.get_text("music.volume_changed", interaction.user.id, interaction.guild_id, volume=int(self.player.volume * 100))
            await interaction.response.send_message(volume_text, ephemeral=True)
        else:
            volume_max_text = translation_manager.get_text("music.volume_max", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(volume_max_text, ephemeral=True)
    
    @discord.ui.button(label="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        if self.player.volume > 0.0:
            self.player.volume = max(0.0, self.player.volume - 0.1)
            if self.player.voice_client and hasattr(self.player.voice_client.source, 'volume'):
                self.player.voice_client.source.volume = self.player.volume
            
            await update_control_message(self.player)
            volume_text = translation_manager.get_text("music.volume_changed", interaction.user.id, interaction.guild_id, volume=int(self.player.volume * 100))
            await interaction.response.send_message(volume_text, ephemeral=True)
        else:
            volume_min_text = translation_manager.get_text("music.volume_min", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(volume_min_text, ephemeral=True)
    
    @discord.ui.button(label="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            return await self.send_permission_error(interaction)
        
        if len(self.player.queue) > 1:
            self.player.shuffle_queue()
            await update_control_message(self.player)
            shuffled_text = translation_manager.get_text("music.queue_shuffled", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(shuffled_text, ephemeral=True)
        else:
            queue_too_short_text = translation_manager.get_text("music.queue_too_short", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(queue_too_short_text, ephemeral=True)
    
    @discord.ui.button(label="❓", style=discord.ButtonStyle.secondary, row=1)
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        help_title = translation_manager.get_text("music.help_title", interaction.user.id, interaction.guild_id)
        embed = discord.Embed(title=help_title, color=discord.Color.blue())
        help_commands_name = translation_manager.get_text("music.help_commands", interaction.user.id, interaction.guild_id)
        help_commands_text = translation_manager.get_text("music.help_commands_text", interaction.user.id, interaction.guild_id)
        embed.add_field(
            name=help_commands_name,
            value=help_commands_text,
            inline=False
        )
        help_buttons_name = translation_manager.get_text("music.help_buttons", interaction.user.id, interaction.guild_id)
        help_buttons_text = translation_manager.get_text("music.help_buttons_text", interaction.user.id, interaction.guild_id)
        embed.add_field(
            name=help_buttons_name,
            value=help_buttons_text,
            inline=False
        )
        help_permissions_name = translation_manager.get_text("music.help_permissions", interaction.user.id, interaction.guild_id)
        help_permissions_text = translation_manager.get_text("music.help_permissions_text", interaction.user.id, interaction.guild_id)
        embed.add_field(
            name=help_permissions_name,
            value=help_permissions_text,
            inline=False
        )
        help_services_name = translation_manager.get_text("music.help_services", interaction.user.id, interaction.guild_id)
        help_services_text = translation_manager.get_text("music.help_services_text", interaction.user.id, interaction.guild_id)
        embed.add_field(
            name=help_services_name,
            value=help_services_text,
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def update_control_message(player):
    if player.control_message:
        if not player.is_playing and player.current_track:
            player.current_track = None
        try:
            await player.control_message.fetch()
            embed = await create_control_embed(player)
            view = MusicControlView(player)
            await player.control_message.edit(embed=embed, view=view)
        except (discord.NotFound, discord.HTTPException):
            player.control_message = None
            if player.text_channel:
                embed = await create_control_embed(player)
                view = MusicControlView(player)
                player.control_message = await player.text_channel.send(embed=embed, view=view)

async def play_next_track(player):
    """Odtwarza następny utwór z kolejki"""
    if not player.voice_client or not player.voice_client.is_connected():
        error_msg = translation_manager.get_text("music.voice_client_error", None, None)
        print(error_msg)
        player.is_playing = False
        return

    if not player.queue:
        player.current_track = None
        player.is_playing = False
        await update_control_message(player)
        await player.schedule_disconnect()
        return

    track_info = player.queue[0]
    debug_msg1 = translation_manager.get_text("debug.playing_from_url", None, None, url=track_info['url'])
    debug_msg2 = translation_manager.get_text("debug.webpage_url", None, None, webpage_url=track_info.get('webpage_url', None))
    print(debug_msg1)
    print(debug_msg2)
    
    try:
        sources = await YTDLSource.from_url(track_info['url'], loop=bot.loop, stream=True, volume=player.volume)
        if not sources or not sources[0]:
            error_msg = translation_manager.get_text("music.audio_source_error", None, None)
            raise ValueError(error_msg)
        
        source = sources[0]
        debug_msg1 = translation_manager.get_text("debug.ytdl_source_url", None, None, url=source.data.get('url', ''))
        debug_msg2 = translation_manager.get_text("debug.ytdl_source_webpage", None, None, webpage_url=source.data.get('webpage_url', ''))
        print(debug_msg1)
        print(debug_msg2)

    except Exception as e:
        error_msg = translation_manager.get_text("music.track_source_error", None, None, title=track_info['title'], error=str(e))
        print(error_msg)
        if player.text_channel:
            playback_error_msg = translation_manager.get_text("music.playback_error", None, None, title=track_info['title'])
            await player.text_channel.send(playback_error_msg)
        
        player.queue.pop(0)
        await play_next_track(player)
        return

    player.queue.pop(0)
    player.current_track = track_info
    player.current_track['start_time'] = datetime.now()
    player.is_playing = True
    player.is_paused = False

    if player.disconnect_timer:
        player.disconnect_timer.cancel()

    def after_playing(error):
        if error:
            print(f'Player error: {error}')
        try:
            coro = play_next_track(player)
            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            fut.result()
        except Exception as e:
            print(f"Error in after_playing: {e}")

    player.voice_client.play(source, after=after_playing)
    playback_started_msg = translation_manager.get_text("music.playback_started", None, None, title=track_info['title'])
    print(playback_started_msg)
    
    await update_control_message(player)

async def stop_music(player):
    """Zatrzymuje muzykę i rozłącza bota"""
    if hasattr(player, 'disconnect_timer') and player.disconnect_timer:
        player.disconnect_timer.cancel()
        player.disconnect_timer = None
    if player.voice_client:
        if player.voice_client.is_playing():
            player.voice_client.stop()
        await player.voice_client.disconnect()
        player.voice_client = None
    
    if player.control_message:
        try:
            await player.control_message.delete()
        except discord.NotFound:
            pass
        player.control_message = None
    
    player.queue.clear()
    player.current_track = None
    player.is_playing = False
    player.is_paused = False

async def check_voice_channel_empty(player):
    if not player.voice_client or not player.voice_client.channel:
        return
    
    members = [m for m in player.voice_client.channel.members if not m.bot]
    
    if not members:
        await asyncio.sleep(10)
        members = [m for m in player.voice_client.channel.members if not m.bot]
        if not members:
            await stop_music(player)

def extract_spotify_info(url):
    """Wyciąga informacje ze Spotify URL i zwraca listę nazw utworów do wyszukania na YouTube"""
    try:
        if not spotify or not hasattr(spotify, '_auth'):
            error_msg = translation_manager.get_text("music.spotify_auth_error", None, None)
            print(error_msg)
            return None
        
        if 'spotify' not in globals():
            return None
        
        spotify_id = None
        if '/track/' in url:
            spotify_id = url.split('/track/')[1].split('?')[0]
            track = spotify.track(spotify_id)
            artist_names = ', '.join([artist['name'] for artist in track['artists']])
            search_query = f"{artist_names} - {track['name']}"
            return [search_query]
            
        elif '/playlist/' in url:
            spotify_id = url.split('/playlist/')[1].split('?')[0]
            playlist = spotify.playlist(spotify_id)
            search_queries = []
            
            for item in playlist['tracks']['items']:
                if item['track'] and item['track']['name']:
                    track = item['track']
                    artist_names = ', '.join([artist['name'] for artist in track['artists']])
                    search_query = f"{artist_names} - {track['name']}"
                    search_queries.append(search_query)
            
            return search_queries
            
        elif '/album/' in url:
            spotify_id = url.split('/album/')[1].split('?')[0]
            album = spotify.album(spotify_id)
            search_queries = []
            
            for track in album['tracks']['items']:
                artist_names = ', '.join([artist['name'] for artist in track['artists']])
                search_query = f"{artist_names} - {track['name']}"
                search_queries.append(search_query)
            
            return search_queries
            
        elif '/artist/' in url:
            spotify_id = url.split('/artist/')[1].split('?')[0]
            top_tracks = spotify.artist_top_tracks(spotify_id, country='PL')
            search_queries = []
            
            for track in top_tracks['tracks']:
                artist_names = ', '.join([artist['name'] for artist in track['artists']])
                search_query = f"{artist_names} - {track['name']}"
                search_queries.append(search_query)
            
            return search_queries
            
    except Exception as e:
        error_msg = translation_manager.get_text("music.spotify_url_error", None, None, error=str(e))
        print(error_msg)
        return None
    
    return None

async def process_spotify_url(url, player, interaction):
    """Przetwarza Spotify URL i dodaje utwory do kolejki"""
    try:
        search_queries = extract_spotify_info(url)
        
        if not search_queries:
            return {'added': 0, 'failed': 1, 'total': 1}
        
        added_count = 0
        failed_count = 0
        
        for query in search_queries:
            try:
                sources = await asyncio.wait_for(
                    YTDLSource.from_url(f"ytsearch1:{query}", loop=bot.loop, volume=player.volume),
                    timeout=10.0
                )
                
                if sources:
                    source = sources[0]
                    debug_msg1 = translation_manager.get_text("debug.ytdl_source_url", None, None, url=source.data.get('url', ''))
                    debug_msg2 = translation_manager.get_text("debug.ytdl_source_webpage", None, None, webpage_url=source.data.get('webpage_url', ''))
                    print(debug_msg1)
                    print(debug_msg2)
                    track_info = {
                        'url': source.data.get('url', ''), 
                        'title': source.title,
                        'duration': source.duration,
                        'requester': interaction.user,
                        'webpage_url': source.data.get('webpage_url', ''),
                        'spotify_query': query
                    }
                    player.add_track(track_info)
                    debug_msg1 = translation_manager.get_text("debug.adding_to_queue_url", None, None, url=track_info['url'])
                    debug_msg2 = translation_manager.get_text("debug.adding_to_queue_webpage", None, None, webpage_url=track_info['webpage_url'])
                    print(debug_msg1)
                    print(debug_msg2)
                    added_count += 1
                else:
                    failed_count += 1
                    
            except asyncio.TimeoutError:
                timeout_msg = translation_manager.get_text("music.search_timeout", None, None, query=query)
                print(timeout_msg)
                failed_count += 1
            except Exception as e:
                error_msg = translation_manager.get_text("music.search_error", None, None, query=query, error=str(e))
                print(error_msg)
                failed_count += 1
        
        return {
            'added': added_count,
            'failed': failed_count,
            'total': len(search_queries)
        }
    except Exception as e:
        error_msg = translation_manager.get_text("music.process_spotify_error", None, None, error=str(e))
        print(error_msg)
        return {'added': 0, 'failed': 1, 'total': 1}

def is_spotify_url(url):
    """Sprawdza czy URL to Spotify i jaki typ"""
    if 'spotify.com' not in url:
        return False
    
    spotify_types = ['/track/', '/playlist/', '/album/', '/artist/']
    return any(spotify_type in url for spotify_type in spotify_types)

def is_youtube_url(url):
    """Sprawdza czy URL to YouTube"""
    return any(domain in url for domain in ['youtube.com', 'youtu.be', 'music.youtube.com'])

@app_commands.guild_only()
@bot.tree.command(name="play", description=get_command_description("play"))
@app_commands.describe(query=get_parameter_description("query"))
async def play_music(interaction: discord.Interaction, query: str):
    if not await check_changelog_and_module(interaction, "music"):
        return
    if not interaction.user.voice or not interaction.user.voice.channel:
        must_be_in_voice_text = translation_manager.get_text("music.must_be_in_voice", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(
            must_be_in_voice_text, ephemeral=True
        )
    
    await interaction.response.defer(ephemeral=True)
    
    player = get_music_player(interaction.guild.id)
    channel = interaction.user.voice.channel

    if player.voice_client is None:
        try:
            player.voice_client = await channel.connect()
            player.text_channel = interaction.channel
        except asyncio.TimeoutError:
            timeout_error_text = translation_manager.get_text("music.connection_timeout", interaction.user.id, interaction.guild_id)
            return await interaction.followup.send(timeout_error_text, ephemeral=True)
        except Exception as e:
            connection_error_text = translation_manager.get_text("music.connection_error", interaction.user.id, interaction.guild_id, error=str(e))
            return await interaction.followup.send(connection_error_text, ephemeral=True)

    if is_spotify_url(query):
        try:
            stats = await process_spotify_url(query, player, interaction)
            
            if stats and stats['added'] > 0:
                if not player.is_playing:
                    await play_next_track(player)
                    
                    if not player.control_message:
                        embed = await create_control_embed(player)
                        view = MusicControlView(player)
                        player.control_message = await interaction.channel.send(embed=embed, view=view)
                else:
                    await update_control_message(player)
                
                title_text = translation_manager.get_text("music.spotify_added", interaction.user.id, interaction.guild_id)
                description_text = translation_manager.get_text("music.tracks_added", interaction.user.id, interaction.guild_id, count=stats['added'])
                embed = discord.Embed(
                    title=title_text,
                    description=description_text,
                    color=discord.Color.green()
                )
                if stats['failed'] > 0:
                    warning_title = translation_manager.get_text("music.spotify_failed", interaction.user.id, interaction.guild_id)
                    warning_text = translation_manager.get_text("music.spotify_failed_text", interaction.user.id, interaction.guild_id, count=stats['failed'])
                    embed.add_field(
                        name=warning_title, 
                        value=warning_text, 
                        inline=False
                    )
                
                return await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                spotify_error_text = translation_manager.get_text("music.spotify_error", interaction.user.id, interaction.guild_id)
                return await interaction.followup.send(spotify_error_text, ephemeral=True)
        except Exception as e:
            error_msg = translation_manager.get_text("music.spotify_url_error", None, None, error=str(e))
            print(error_msg)
            spotify_processing_error_text = translation_manager.get_text("music.spotify_processing_error", interaction.user.id, interaction.guild_id)
            return await interaction.followup.send(spotify_processing_error_text, ephemeral=True)

    try:
        sources = await YTDLSource.from_url(query, loop=bot.loop, volume=player.volume)

        valid_sources = [s for s in sources if s is not None]
        if not valid_sources:
            track_processing_error_text = translation_manager.get_text("music.track_processing_error", interaction.user.id, interaction.guild_id)
            return await interaction.followup.send(track_processing_error_text)

        added_count = 0
        for source in valid_sources:
            track_info = {
                'url': source.data.get('url', ''),
                'title': source.title,
                'duration': source.duration,
                'requester': interaction.user,
                'webpage_url': source.data.get('webpage_url', '')
            }
            player.add_track(track_info)
            debug_msg1 = translation_manager.get_text("debug.adding_to_queue_url", None, None, url=track_info['url'])
            debug_msg2 = translation_manager.get_text("debug.adding_to_queue_webpage", None, None, webpage_url=track_info['webpage_url'])
            print(debug_msg1)
            print(debug_msg2)
            added_count += 1
    except Exception as e:
        print(f"Error processing track: {e}")
        track_processing_failed_text = translation_manager.get_text("music.track_processing_failed", interaction.user.id, interaction.guild_id)
        return await interaction.followup.send(track_processing_failed_text)
    
    if not player.is_playing:
        await play_next_track(player)
        
        if not player.control_message:
            embed = await create_control_embed(player)
            view = MusicControlView(player)
            player.control_message = await interaction.channel.send(embed=embed, view=view)
    else:
        await update_control_message(player)
    
    if added_count == 1:
        track_added_title = translation_manager.get_text("music.track_added", interaction.user.id, interaction.guild_id)
        embed = discord.Embed(
            title=track_added_title,
            description=f"**{sources[0].title}**",
            color=discord.Color.green()
        )
    else:
        playlist_added_title = translation_manager.get_text("music.playlist_added", interaction.user.id, interaction.guild_id)
        playlist_added_description = translation_manager.get_text("music.tracks_added", interaction.user.id, interaction.guild_id, count=added_count)
        embed = discord.Embed(
            title=playlist_added_title,
            description=playlist_added_description,
            color=discord.Color.green()
        )
    
    await interaction.followup.send(embed=embed)

@app_commands.guild_only()
@bot.tree.command(name="queue", description=get_command_description("queue"))
async def show_queue(interaction: discord.Interaction):
    if not await check_changelog_and_module(interaction, "music"):
        return
    await interaction.response.defer(ephemeral=True)
    
    player = get_music_player(interaction.guild.id)
    queue_title = translation_manager.get_text("music.queue_title", interaction.user.id, interaction.guild_id)
    embed = discord.Embed(title=queue_title, color=discord.Color.blue())
    
    if player.current_track:
        current = player.current_track
        elapsed = (datetime.now() - current.get('start_time', datetime.now())).total_seconds()
        remaining = max(current.get('duration', 0) - elapsed, 0)
        minutes, seconds = divmod(int(remaining), 60)
        
        now_playing_title = translation_manager.get_text("music.now_playing", interaction.user.id, interaction.guild_id)
        remaining_text = translation_manager.get_text("music.remaining_time", interaction.user.id, interaction.guild_id, minutes=minutes, seconds=seconds)
        embed.add_field(
            name=now_playing_title,
            value=f"**{current['title']}**\n"
                  f"{translation_manager.get_text('music.added_by', interaction.user.id, interaction.guild_id)}: {current['requester'].mention}\n"
                  f"{remaining_text}",
            inline=False
        )
    else:
        now_playing_title = translation_manager.get_text("music.now_playing", interaction.user.id, interaction.guild_id)
        nothing_playing_text = translation_manager.get_text("music.nothing_playing", interaction.user.id, interaction.guild_id)
        embed.add_field(name=now_playing_title, value=nothing_playing_text, inline=False)
    
    if player.queue:
        queue_text = ""
        for i, track in enumerate(player.queue[:10], 1): 
            duration = track.get('duration', 0)
            minutes, seconds = divmod(duration, 60)
            queue_text += f"{i}. **{track['title']}** ({minutes}:{seconds:02d})\n"
        
        if len(player.queue) > 10:
            more_text = translation_manager.get_text("music.and_more", interaction.user.id, interaction.guild_id, count=len(player.queue) - 10)
            queue_text += f"\n{more_text}"
        
        queue_label = translation_manager.get_text("music.queue_label", interaction.user.id, interaction.guild_id)
        embed.add_field(name=queue_label, value=queue_text, inline=False)
    else:
        queue_label = translation_manager.get_text("music.queue_label", interaction.user.id, interaction.guild_id)
        queue_empty_text = translation_manager.get_text("music.queue_empty", interaction.user.id, interaction.guild_id)
        embed.add_field(name=queue_label, value=queue_empty_text, inline=False)
    
    volume_label = translation_manager.get_text("music.volume_label", interaction.user.id, interaction.guild_id)
    status_label = translation_manager.get_text("music.status_label", interaction.user.id, interaction.guild_id)
    status_text = translation_manager.get_text("music.status_playing" if player.is_playing else "music.status_stopped", interaction.user.id, interaction.guild_id)
    embed.add_field(name=volume_label, value=f"{int(player.volume * 100)}%", inline=True)
    embed.add_field(name=status_label, value=status_text, inline=True)
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="skip", description=get_command_description("skip"))
async def skip_track(interaction: discord.Interaction):
    if not await check_changelog_and_module(interaction, "music"):
        return
    player = get_music_player(interaction.guild.id)
    
    if not (interaction.user.guild_permissions.administrator or 
            (interaction.user.voice and player.voice_client and 
             interaction.user.voice.channel == player.voice_client.channel)):
        must_be_in_voice_or_admin_text = translation_manager.get_text("music.must_be_in_voice_or_admin", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(
            must_be_in_voice_or_admin_text,
            ephemeral=True
        )
    
    if player.voice_client and player.voice_client.is_playing():
        player.voice_client.stop()
        track_skipped_text = translation_manager.get_text("music.track_skipped", interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(track_skipped_text, ephemeral=True)
    else:
        nothing_playing_text = translation_manager.get_text("music.nothing_playing", interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(nothing_playing_text, ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="stop", description=get_command_description("stop"))
async def stop_command(interaction: discord.Interaction):
    if not await check_changelog_and_module(interaction, "music"):
        return
    player = get_music_player(interaction.guild.id)
    
    if not (interaction.user.guild_permissions.administrator or 
            (interaction.user.voice and player.voice_client and 
             interaction.user.voice.channel == player.voice_client.channel)):
        must_be_in_voice_or_admin_text = translation_manager.get_text("music.must_be_in_voice_or_admin", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(
            must_be_in_voice_or_admin_text,
            ephemeral=True
        )
    
    await stop_music(player)
    playback_stopped_text = translation_manager.get_text("music.playback_stopped", interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(playback_stopped_text, ephemeral=True)

# ==================== ECONOMY COMMANDS ====================

@app_commands.guild_only()
@bot.tree.command(name="seteconlogs", description=get_command_description("seteconlogs"))
@app_commands.describe(channel=get_parameter_description("econlogs_channel"))
async def set_econlogs(inter: discord.Interaction, channel: discord.TextChannel):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not inter.user.guild_permissions.administrator:
        no_perms_text = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms_text), ephemeral=True)
    path = "channels/econlogs.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            econlogs = json.load(f)
    else:
        econlogs = {}
    econlogs[str(inter.guild.id)] = channel.id
    with open(path, "w") as f:
        json.dump(econlogs, f)
    econlogs_set_text = translation_manager.get_text("economy.econlogs_set", inter.user.id, inter.guild.id, channel=channel.mention)
    await inter.response.send_message(embed=eco_success(econlogs_set_text), ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="balance", description=get_command_description("balance"))
async def balance_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    await inter.response.defer(ephemeral=True)
    user = get_user_eco(inter.guild.id, inter.user.id)
    title_text = translation_manager.get_text("economy.balance_title", inter.user.id, inter.guild.id, username=inter.user.name)
    wallet_text = translation_manager.get_text("economy.wallet", inter.user.id, inter.guild.id)
    bank_text = translation_manager.get_text("economy.bank", inter.user.id, inter.guild.id)
    embed = discord.Embed(title=title_text, color=discord.Color.gold())
    embed.add_field(name=wallet_text, value=f"{user['balance']}$", inline=True)
    embed.add_field(name=bank_text, value=f"{user['bank']}$", inline=True)
    await inter.followup.send(embed=embed, ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="deposit", description=get_command_description("deposit"))
@app_commands.describe(amount=get_parameter_description("amount_deposit"))
async def deposit_cmd(inter: discord.Interaction, amount: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if amount <= 0:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.invalid_amount", inter.user.id, inter.guild_id)), ephemeral=True)
    user = get_user_eco(inter.guild.id, inter.user.id)
    if user['balance'] < amount:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.insufficient_funds", inter.user.id, inter.guild_id)), ephemeral=True)
    fee = math.floor(amount * 0.05)
    after_fee = amount - fee
    user['balance'] -= amount
    user['bank'] += after_fee
    update_user_eco(inter.guild.id, inter.user.id, user)
    log_msg = translation_manager.get_text("logging.economy_deposit", None, None, user=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=after_fee, fee=fee)
    log_econ(inter.guild.id, log_msg, inter.user.id)
    deposit_success_text = translation_manager.get_text("economy.deposit_success", inter.user.id, inter.guild_id, amount=after_fee, fee=fee)
    return await inter.response.send_message(embed=eco_success(deposit_success_text))

@app_commands.guild_only()
@bot.tree.command(name="withdraw", description=get_command_description("withdraw"))
@app_commands.describe(amount=get_parameter_description("amount_withdraw"))
async def withdraw_cmd(inter: discord.Interaction, amount: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if amount <= 0:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.invalid_amount", inter.user.id, inter.guild_id)), ephemeral=True)
    user = get_user_eco(inter.guild.id, inter.user.id)
    if user['bank'] < amount:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.insufficient_bank", inter.user.id, inter.guild_id)), ephemeral=True)

    user['bank'] -= amount
    user['balance'] += amount
    update_user_eco(inter.guild.id, inter.user.id, user)
    log_msg = translation_manager.get_text("logging.economy_withdraw", None, None, user=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=amount)
    log_econ(inter.guild.id, log_msg, inter.user.id)
    withdraw_success_text = translation_manager.get_text("economy.withdraw_success", inter.user.id, inter.guild_id, amount=amount)
    return await inter.response.send_message(embed=eco_success(withdraw_success_text))

@app_commands.guild_only()
@bot.tree.command(name="admin_add", description=get_command_description("admin_add"))
@app_commands.describe(user=get_parameter_description("user"), amount=get_parameter_description("amount"), konto=get_parameter_description("konto"))
async def admin_add(inter: discord.Interaction, user: discord.Member, amount: int, konto: str):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not is_admin(inter.user):
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    if amount <= 0 or konto not in ["balance", "bank"]:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.invalid_parameters", inter.user.id, inter.guild_id)), ephemeral=True)
    data = get_user_eco(inter.guild.id, user.id)
    data[konto] += amount
    update_user_eco(inter.guild.id, user.id, data)
    log_msg = translation_manager.get_text("logging.economy_admin_add", None, None, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=amount, account=konto, user=user.mention)
    log_econ(inter.guild.id, log_msg, user.id)
    admin_add_success_text = translation_manager.get_text("economy.admin_add_success", inter.user.id, inter.guild_id, amount=amount, account=konto, user=user.mention)
    return await inter.response.send_message(embed=eco_success(admin_add_success_text), ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="admin_remove", description=get_command_description("admin_remove"))
@app_commands.describe(user=get_parameter_description("user"), amount=get_parameter_description("amount"), konto=get_parameter_description("konto"))
async def admin_remove(inter: discord.Interaction, user: discord.Member, amount: int, konto: str):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not is_admin(inter.user):
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    if amount <= 0 or konto not in ["balance", "bank"]:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.invalid_parameters", inter.user.id, inter.guild_id)), ephemeral=True)
    data = get_user_eco(inter.guild.id, user.id)
    if data[konto] < amount:
        insufficient_funds_text = translation_manager.get_text("general.user_insufficient_funds", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(insufficient_funds_text), ephemeral=True)
    data[konto] -= amount
    update_user_eco(inter.guild.id, user.id, data)
    log_msg = translation_manager.get_text("logging.economy_admin_remove", None, None, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=amount, account=konto, user=user.mention)
    log_econ(inter.guild.id, log_msg, user.id)
    admin_remove_success_text = translation_manager.get_text("economy.admin_remove_success", inter.user.id, inter.guild_id, amount=amount, account=konto, user=user.mention)
    return await inter.response.send_message(embed=eco_success(admin_remove_success_text), ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="admin_setincome", description=get_command_description("admin_setincome"))
@app_commands.describe(role=get_parameter_description("role"), amount=get_parameter_description("income_amount"))
async def admin_setincome(inter: discord.Interaction, role: discord.Role, amount: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not is_admin(inter.user):
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    eco = load_economy(inter.guild.id)
    eco['incomes'][str(role.id)] = amount
    save_economy(inter.guild.id, eco)
    log_msg = translation_manager.get_text("logging.economy_income_set", None, None, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=amount, role=role.name)
    log_econ(inter.guild.id, log_msg)
    income_set_text = translation_manager.get_text("economy.income_set", inter.user.id, inter.guild_id, amount=amount, role=role.mention)
    return await inter.response.send_message(embed=eco_success(income_set_text), ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="admin_listincome", description=get_command_description("admin_listincome"))
async def admin_listincome(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not is_admin(inter.user):
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    eco = load_economy(inter.guild.id)
    incomes = eco.get('incomes', {})
    income_roles_title = translation_manager.get_text("economy.income_roles_title", inter.user.id, inter.guild_id)
    embed = discord.Embed(title=income_roles_title, color=discord.Color.blue())
    if not incomes:
        no_income_roles_text = translation_manager.get_text("economy.no_income_roles", inter.user.id, inter.guild_id)
        embed.description = no_income_roles_text
    else:
        for rid, val in incomes.items():
            role = inter.guild.get_role(int(rid))
            embed.add_field(name=role.name if role else rid, value=f"{val}$", inline=False)
    await inter.response.send_message(embed=embed, ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="admin_removeincome", description=get_command_description("admin_removeincome"))
@app_commands.describe(role=get_parameter_description("role"))
async def admin_removeincome(inter: discord.Interaction, role: discord.Role):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not is_admin(inter.user):
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    eco = load_economy(inter.guild.id)
    if str(role.id) in eco['incomes']:
        del eco['incomes'][str(role.id)]
        save_economy(inter.guild.id, eco)
        log_msg = translation_manager.get_text("logging.economy_income_removed", None, None, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", role=role.name)
        log_econ(inter.guild.id, log_msg)
        income_removed_text = translation_manager.get_text("economy.income_removed", inter.user.id, inter.guild_id, role=role.mention)
        return await inter.response.send_message(embed=eco_success(income_removed_text), ephemeral=True)
    no_income_role_text = translation_manager.get_text("economy.role_no_income", inter.user.id, inter.guild_id)
    return await inter.response.send_message(embed=eco_error(no_income_role_text), ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="collect", description=get_command_description("collect"))
async def collect_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    user = get_user_eco(inter.guild.id, inter.user.id)
    eco = load_economy(inter.guild.id)
    now = int(datetime.now(timezone.utc).timestamp())
    if now - user.get('last_daily', 0) < 86400:
        diff = 86400 - (now - user['last_daily'])
        h, m = divmod(diff // 60, 60)
        income_cooldown_text = translation_manager.get_text("economy.income_cooldown", inter.user.id, inter.guild_id, hours=h, minutes=m)
        return await inter.response.send_message(embed=eco_error(income_cooldown_text), ephemeral=True)
    total, found = role_income_sum(inter.guild, inter.user, eco)
    if not found:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.no_income_roles_user", inter.user.id, inter.guild_id)), ephemeral=True)
    user['balance'] += total
    user['last_daily'] = now
    update_user_eco(inter.guild.id, inter.user.id, user)
    role_list = ', '.join(f"{r.mention} ({amt}$)" for r, amt in found)
    income_collected_title = translation_manager.get_text("economy.income_collected_title", inter.user.id, inter.guild_id)
    embed = discord.Embed(title=income_collected_title, color=discord.Color.green())
    sum_field_name = translation_manager.get_text("economy.income_sum", inter.user.id, inter.guild_id)
    roles_field_name = translation_manager.get_text("economy.income_roles", inter.user.id, inter.guild_id)
    embed.add_field(name=sum_field_name, value=f"{total}$", inline=False)
    embed.add_field(name=roles_field_name, value=role_list, inline=False)
    log_msg = translation_manager.get_text("logging.economy_daily_claimed", None, None, user=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=total, roles=role_list)
    log_econ(inter.guild.id, log_msg, inter.user.id)
    await inter.response.send_message(embed=embed)

@app_commands.guild_only()
@bot.tree.command(name="work", description=get_command_description("work"))
async def work_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    user = get_user_eco(inter.guild.id, inter.user.id)
    eco = load_economy(inter.guild.id)
    now = int(datetime.now(timezone.utc).timestamp())
    if now - user.get('last_work', 0) < 3600:
        diff = 3600 - (now - user['last_work'])
        m, s = divmod(diff, 60)
        work_cooldown_text = translation_manager.get_text("economy.work_cooldown", inter.user.id, inter.guild_id, minutes=m, seconds=s)
        return await inter.response.send_message(embed=eco_error(work_cooldown_text), ephemeral=True)
    minr, maxr = eco.get('work', [10, 100])
    earn = random.randint(minr, maxr)
    user['balance'] += earn
    user['last_work'] = now
    update_user_eco(inter.guild.id, inter.user.id, user)
    log_msg = translation_manager.get_text("logging.economy_work", None, None, user=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", amount=earn)
    log_econ(inter.guild.id, log_msg, inter.user.id)
    work_success_text = translation_manager.get_text("economy.work_success", inter.user.id, inter.guild_id, amount=earn)
    return await inter.response.send_message(embed=eco_success(work_success_text))

@app_commands.guild_only()
@bot.tree.command(name="setwork", description=get_command_description("setwork"))
@app_commands.describe(min=get_parameter_description("min_amount"), max=get_parameter_description("max_amount"))
async def setwork_cmd(inter: discord.Interaction, min: int, max: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not is_admin(inter.user):
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    if min < 0 or max <= min:
        return await inter.response.send_message(embed=eco_error(translation_manager.get_text("economy.work_invalid_range", inter.user.id, inter.guild_id)), ephemeral=True)
    eco = load_economy(inter.guild.id)
    eco['work'] = [min, max]
    save_economy(inter.guild.id, eco)
    log_msg = translation_manager.get_text("logging.economy_work_range_set", None, None, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", min=min, max=max)
    log_econ(inter.guild.id, log_msg)
    work_range_set_text = translation_manager.get_text("economy.work_range_set", inter.user.id, inter.guild_id, min=min, max=max)
    await inter.response.send_message(embed=eco_success(work_range_set_text), ephemeral=True)

@app_commands.guild_only()
@bot.tree.command(name="steal", description=get_command_description("steal"))
@app_commands.describe(user=get_parameter_description("steal_target"))
async def steal_cmd(inter: discord.Interaction, user: discord.Member):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if user.id == inter.user.id:
        cant_steal_self_text = translation_manager.get_text("economy.cant_steal_self", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(cant_steal_self_text), ephemeral=True)
    author = get_user_eco(inter.guild.id, inter.user.id)
    victim = get_user_eco(inter.guild.id, user.id)
    now = int(datetime.now(timezone.utc).timestamp())
    if now - author.get('last_steal', 0) < 900:
        diff = 900 - (now - author['last_steal'])
        m, s = divmod(diff, 60)
        cooldown_text = translation_manager.get_text("economy.steal_cooldown", inter.user.id, inter.guild_id, minutes=m, seconds=s)
        return await inter.response.send_message(embed=eco_error(cooldown_text), ephemeral=True)
    if victim['balance'] < 1:
        victim_no_money_text = translation_manager.get_text("economy.victim_no_money", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(victim_no_money_text), ephemeral=True)
    chance = random.randint(1, 100)
    success = False
    if chance <= 40:
        stolen = math.floor(victim['balance'] * 0.2)
        if stolen < 1: stolen = 1
        victim['balance'] -= stolen
        author['balance'] += stolen
        msg = translation_manager.get_text("economy.steal_success", inter.user.id, inter.guild_id, amount=stolen, user=user.mention)
        success = True
    else:
        lost = math.floor(author['balance'] * 0.1)
        if lost < 1: lost = 1 if author['balance'] > 0 else 0
        author['balance'] -= lost
        victim['balance'] += lost
        msg = translation_manager.get_text("economy.steal_failed", inter.user.id, inter.guild_id, amount=lost, user=user.mention) 
    author['last_steal'] = now
    update_user_eco(inter.guild.id, inter.user.id, author)
    update_user_eco(inter.guild.id, user.id, victim)
    if success:
        log_msg1 = translation_manager.get_text("logging.economy_steal_success", None, None, thief=inter.user.mention, victim=user.mention, amount=stolen)
        log_msg2 = translation_manager.get_text("logging.economy_steal_victim", None, None, victim=user.mention, thief=inter.user.mention, amount=stolen)
        log_econ(inter.guild.id, log_msg1, inter.user.id)
        log_econ(inter.guild.id, log_msg2, user.id)
    else:
        log_msg1 = translation_manager.get_text("logging.economy_steal_failed", None, None, thief=inter.user.mention, victim=user.mention, amount=lost)
        log_msg2 = translation_manager.get_text("logging.economy_steal_caught", None, None, victim=user.mention, thief=inter.user.mention, amount=lost)
        log_econ(inter.guild.id, log_msg1, inter.user.id)
        log_econ(inter.guild.id, log_msg2, user.id)
    await inter.response.send_message(embed=eco_success(msg))

@app_commands.guild_only()
@bot.tree.command(name="check", description=get_command_description("check"))
@app_commands.describe(user=get_parameter_description("user"), show_bank=get_parameter_description("show_bank"))
async def check_cmd(inter: discord.Interaction, user: discord.Member, show_bank: bool = False):
    if not await check_changelog_and_module(inter, "economy"):
        return
    req_is_admin = is_admin(inter.user)
    data = get_user_eco(inter.guild.id, user.id)
    balance_title = translation_manager.get_text("economy.balance_title", inter.user.id, inter.guild_id, username=user.display_name)
    wallet_label = translation_manager.get_text("economy.wallet", inter.user.id, inter.guild_id)
    bank_label = translation_manager.get_text("economy.bank", inter.user.id, inter.guild_id)
    embed = discord.Embed(title=balance_title, color=discord.Color.blue())
    embed.add_field(name=wallet_label, value=f"{data['balance']}$", inline=True)
    if show_bank and req_is_admin:
        embed.add_field(name=bank_label, value=f"{data['bank']}$", inline=True)
    elif show_bank and not req_is_admin:
        only_admin_bank_text = translation_manager.get_text("economy.only_admin_bank", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(only_admin_bank_text), ephemeral=True)
    await inter.response.send_message(embed=embed)

@app_commands.guild_only()
@bot.tree.command(name="baltop", description=get_command_description("baltop"))
async def baltop_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    eco = load_economy(inter.guild.id)
    ranking = sorted(eco['users'].items(), key=lambda i: i[1].get('balance', 0), reverse=True)[:10]
    baltop_title = translation_manager.get_text("economy.baltop_title", inter.user.id, inter.guild_id)
    embed = discord.Embed(title=baltop_title, color=discord.Color.gold())
    if not ranking:
        no_users_text = translation_manager.get_text("economy.no_users", inter.user.id, inter.guild_id)
        embed.description = no_users_text
    else:
        for pos, (uid, data) in enumerate(ranking, 1):
            member = inter.guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            embed.add_field(name=f"{pos}. {name}", value=f"{data['balance']}$", inline=False)
    await inter.response.send_message(embed=embed)

# ==================== GAMBLING SYSTEM ====================

active_gambling_sessions = {}

def is_user_in_game(guild_id, user_id):
    eco = get_user_eco(guild_id, user_id)
    return eco.get('in_game', False)

def set_user_game(guild_id, user_id, val: bool):
    eco = get_user_eco(guild_id, user_id)
    eco['in_game'] = val
    update_user_eco(guild_id, user_id, eco)

async def block_game(inter, *user_ids):
    for uid in user_ids:
        if is_user_in_game(inter.guild.id, uid):
            already_gambling_text = translation_manager.get_text("general.already_in_gambling", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(already_gambling_text), ephemeral=True)
    for uid in user_ids:
        set_user_game(inter.guild.id, uid, True)
    return None

async def unblock_game(guild_id, *user_ids):
    for uid in user_ids:
        set_user_game(guild_id, uid, False)

def ensure_bet(user, bet):
    if bet <= 0:
        error_msg = translation_manager.get_text("errors.correct_amount", None, None)
        return False, error_msg
    if user['balance'] < bet:
        insufficient_text = translation_manager.get_text("economy.insufficient_wallet", None, None)
        return False, insufficient_text
    return True, None

# RPS (Rock Paper Scissors)
class RPSView(ui.View):
    def __init__(self, starter, target, bet, guild_id):
        super().__init__(timeout=60)
        self.starter = starter
        self.target = target
        self.bet = bet
        self.guild_id = guild_id
        self.choices = {}
        self.msg = None
        self.stopped = False
        self.label_set = False

    async def interaction_check(self, interaction):
        return interaction.user.id in [self.starter.id, self.target.id if self.target else self.starter.id]

    @ui.button(label="🪨", style=ButtonStyle.primary, row=0)
    async def rock(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, "rock")

    @ui.button(label="📄", style=ButtonStyle.primary, row=0)
    async def paper(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, "paper")

    @ui.button(label="✂️", style=ButtonStyle.primary, row=0)
    async def scissors(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, "scissors")

    @ui.button(label="❌ Decline", style=ButtonStyle.danger, row=1)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if self.stopped: return
        eco_starter = get_user_eco(self.guild_id, self.starter.id)
        eco_starter['balance'] += self.bet
        update_user_eco(self.guild_id, self.starter.id, eco_starter)
        if self.target:
            eco_target = get_user_eco(self.guild_id, self.target.id)
            eco_target['balance'] += self.bet
            update_user_eco(self.guild_id, self.target.id, eco_target)
            log_msg1 = translation_manager.get_text("logging.gambling_rps_rejected", None, None, user=self.starter.mention)
            log_msg2 = translation_manager.get_text("logging.gambling_rps_rejected", None, None, user=self.target.mention)
            log_econ(self.guild_id, log_msg1, self.starter.id)
            log_econ(self.guild_id, log_msg2, self.target.id)
        else:
            log_msg = translation_manager.get_text("logging.gambling_rps_rejected", None, None, user=self.starter.mention)
            log_econ(self.guild_id, log_msg, self.starter.id)
        game_rejected_text = translation_manager.get_text("gambling.game_rejected", self.starter.id, self.guild_id)
        await self.msg.edit(content=game_rejected_text, embed=None, view=None)
        set_user_game(self.guild_id, self.starter.id, False)
        if self.target: set_user_game(self.guild_id, self.target.id, False)
        self.stopped = True
        self.stop()

    async def _choose(self, inter, choice):
        if self.stopped: return
        uid = inter.user.id
        if uid in self.choices:
            already_chose_text = translation_manager.get_text("general.already_chose", inter.user.id, inter.guild_id)
            await inter.response.send_message(already_chose_text, ephemeral=True)
            return
        self.choices[uid] = choice
        choice_made_text = translation_manager.get_text("general.choice_made", inter.user.id, inter.guild_id, choice=choice)
        await inter.response.send_message(choice_made_text, ephemeral=True)
        
        if not self.target:
            bot_choice = random.choice(['rock', 'paper', 'scissors'])
            winner = rps_winner(choice, bot_choice)
            eco = get_user_eco(self.guild_id, self.starter.id)
            if winner == 0:
                eco['balance'] += self.bet
                title_text = translation_manager.get_text("gambling.rps_tie", self.starter.id, self.guild_id)
                description_text = translation_manager.get_text("gambling.rps_tie_text", self.starter.id, self.guild_id, choice=choice)
                embed = Embed(title=title_text, description=description_text, color=0xFFA500)
            elif winner == 1:
                winnings = int(self.bet * 1.5)
                eco['balance'] += winnings
                win_title = translation_manager.get_text("gambling.rps_win", self.starter.id, self.guild_id)
                win_desc = translation_manager.get_text("gambling.rps_win_desc", self.starter.id, self.guild_id, bot_choice=bot_choice, amount=winnings - self.bet)
                embed = Embed(title=win_title, description=win_desc, color=0x22dd66)
            else:
                loss = int(self.bet * 0.5)
                eco['balance'] += loss
                lose_title = translation_manager.get_text("gambling.rps_loss", self.starter.id, self.guild_id)
                lose_desc = translation_manager.get_text("gambling.rps_lose_desc", self.starter.id, self.guild_id, bot_choice=bot_choice, amount=loss)
                embed = Embed(title=lose_title, description=lose_desc, color=0xdd2222)
            update_user_eco(self.guild_id, self.starter.id, eco)
            if winner == 0:
                log_msg = translation_manager.get_text("logging.gambling_rps_bot_tie", None, None, user=inter.user.mention)
                log_econ(inter.guild.id, log_msg, inter.user.id)
            elif winner == 1:
                log_msg = translation_manager.get_text("logging.gambling_rps_bot_win", None, None, user=inter.user.mention, amount=winnings - self.bet)
                log_econ(inter.guild.id, log_msg, inter.user.id)
            else:
                log_msg = translation_manager.get_text("logging.gambling_rps_bot_loss", None, None, user=inter.user.mention, amount=loss)
                log_econ(inter.guild.id, log_msg, inter.user.id)
            await self.msg.edit(content=None, embed=embed, view=None)
            set_user_game(self.guild_id, self.starter.id, False)
            self.stopped = True
            self.stop()
            return
        
        if len(self.choices) == 2:
            a_id, b_id = self.starter.id, self.target.id
            a_choice = self.choices[a_id]
            b_choice = self.choices[b_id]
            eco_a = get_user_eco(self.guild_id, a_id)
            eco_b = get_user_eco(self.guild_id, b_id)
            winner = rps_winner(a_choice, b_choice)
            if winner == 0:
                eco_a['balance'] += self.bet
                eco_b['balance'] += self.bet
                title_text = translation_manager.get_text("gambling.rps_tie_embed", self.starter.id, self.guild_id)
                desc_text = translation_manager.get_text("gambling.rps_tie_desc", self.starter.id, self.guild_id, 
                                                        starter=self.starter.mention, target=self.target.mention, 
                                                        starter_choice=a_choice, target_choice=b_choice)
                embed = Embed(title=title_text, description=desc_text, color=0xFFA500)
            elif winner == 1:
                wygrana = int(self.bet * 1.5)
                loss = int(self.bet * 0.5)
                eco_a['balance'] += wygrana
                eco_b['balance'] += loss
                title_text = translation_manager.get_text("gambling.rps_win_embed", self.starter.id, self.guild_id)
                desc_text = translation_manager.get_text("gambling.rps_win_desc", self.starter.id, self.guild_id,
                                                        starter=self.starter.mention, target=self.target.mention,
                                                        starter_choice=a_choice, target_choice=b_choice,
                                                        winner=self.starter.mention, winnings=wygrana)
                embed = Embed(title=title_text, description=desc_text, color=0x22dd66)
            else:
                wygrana = int(self.bet * 1.5)
                loss = int(self.bet * 0.5)
                eco_b['balance'] += wygrana
                eco_a['balance'] += loss
                title_text = translation_manager.get_text("gambling.rps_win_embed", self.starter.id, self.guild_id)
                desc_text = translation_manager.get_text("gambling.rps_win_desc", self.starter.id, self.guild_id,
                                                        starter=self.starter.mention, target=self.target.mention,
                                                        starter_choice=a_choice, target_choice=b_choice,
                                                        winner=self.target.mention, winnings=wygrana)
                embed = Embed(title=title_text, description=desc_text, color=0x22dd66)
            update_user_eco(self.guild_id, a_id, eco_a)
            update_user_eco(self.guild_id, b_id, eco_b)
            if winner == 0:
                log_msg1 = translation_manager.get_text("logging.gambling_rps_player_tie", None, None, player1=self.starter.mention, player2=self.target.mention)
                log_msg2 = translation_manager.get_text("logging.gambling_rps_player_tie", None, None, player1=self.target.mention, player2=self.starter.mention)
                log_econ(inter.guild.id, log_msg1, self.starter.id)
                log_econ(inter.guild.id, log_msg2, self.target.id)
            elif winner == 1:
                log_msg1 = translation_manager.get_text("logging.gambling_rps_player_win", None, None, winner=self.starter.mention, loser=self.target.mention, amount=wygrana - self.bet)
                log_msg2 = translation_manager.get_text("logging.gambling_rps_player_loss", None, None, loser=self.target.mention, winner=self.starter.mention, amount=loss)
                log_econ(inter.guild.id, log_msg1, self.starter.id)
                log_econ(inter.guild.id, log_msg2, self.target.id)
            else:
                log_msg1 = translation_manager.get_text("logging.gambling_rps_player_win", None, None, winner=self.target.mention, loser=self.starter.mention, amount=wygrana - self.bet)
                log_msg2 = translation_manager.get_text("logging.gambling_rps_player_loss", None, None, loser=self.starter.mention, winner=self.target.mention, amount=loss)
                log_econ(inter.guild.id, log_msg1, self.target.id)
                log_econ(inter.guild.id, log_msg2, self.starter.id)
            await self.msg.edit(content=None, embed=embed, view=None)
            set_user_game(self.guild_id, a_id, False)
            set_user_game(self.guild_id, b_id, False)
            self.stopped = True
            self.stop()

    async def on_timeout(self):
        if self.stopped: return
        eco_starter = get_user_eco(self.guild_id, self.starter.id)
        eco_starter['balance'] += self.bet
        update_user_eco(self.guild_id, self.starter.id, eco_starter)
        if self.target:
            eco_target = get_user_eco(self.guild_id, self.target.id)
            eco_target['balance'] += self.bet
            update_user_eco(self.guild_id, self.target.id, eco_target)
            log_msg1 = translation_manager.get_text("logging.gambling_rps_timeout", None, None, user=self.starter.mention)
            log_msg2 = translation_manager.get_text("logging.gambling_rps_timeout", None, None, user=self.target.mention)
            log_econ(self.guild_id, log_msg1, self.starter.id)
            log_econ(self.guild_id, log_msg2, self.target.id)
        else:
            log_msg = translation_manager.get_text("logging.gambling_rps_timeout", None, None, user=self.starter.mention)
            log_econ(self.guild_id, log_msg, self.starter.id)
        try:
            game_timeout_text = translation_manager.get_text("gambling.game_timeout", self.starter.id, self.guild_id)
            await self.msg.edit(content=game_timeout_text, embed=None, view=None)
        except: pass
        set_user_game(self.guild_id, self.starter.id, False)
        if self.target: set_user_game(self.guild_id, self.target.id, False)
        self.stopped = True
        self.stop()

def rps_winner(a, b):
    if a == b: return 0
    if (a, b) in [('rock','scissors'), ('paper','rock'), ('scissors','paper')]: return 1
    return 2

@app_commands.guild_only()
@bot.tree.command(name="rps", description=get_command_description("rps"))
@app_commands.describe(amount=get_parameter_description("amount"), user=get_parameter_description("opponent"))
async def rps_cmd(inter: discord.Interaction, amount: int, user: Optional[discord.Member] = None):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if user and user.id == inter.user.id:
        cannot_play_yourself_text = translation_manager.get_text("general.cannot_play_yourself", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(cannot_play_yourself_text), ephemeral=True)
    if user and user.bot:
        cannot_play_bot_text = translation_manager.get_text("general.cannot_play_bot", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(cannot_play_bot_text), ephemeral=True)
    eco = get_user_eco(inter.guild.id, inter.user.id)
    valid, msg = ensure_bet(eco, amount)
    if not valid:
        return await inter.response.send_message(embed=eco_error(msg), ephemeral=True)
    if is_user_in_game(inter.guild.id, inter.user.id):
        already_gambling = translation_manager.get_text("errors.already_gambling", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(already_gambling), ephemeral=True)
    eco['balance'] -= amount
    update_user_eco(inter.guild.id, inter.user.id, eco)
    if user:
        eco2 = get_user_eco(inter.guild.id, user.id)
        valid2, msg2 = ensure_bet(eco2, amount)
        if not valid2:
            eco['balance'] += amount
            update_user_eco(inter.guild.id, inter.user.id, eco)
            other_player_no_money_text = translation_manager.get_text("gambling.other_player_no_money", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(other_player_no_money_text), ephemeral=True)
        if is_user_in_game(inter.guild.id, user.id):
            eco['balance'] += amount
            update_user_eco(inter.guild.id, inter.user.id, eco)
            other_player_gambling_text = translation_manager.get_text("gambling.other_player_gambling", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(other_player_gambling_text), ephemeral=True)
        eco2['balance'] -= amount
        update_user_eco(inter.guild.id, user.id, eco2)
        set_user_game(inter.guild.id, inter.user.id, True)
        set_user_game(inter.guild.id, user.id, True)
        view = RPSView(inter.user, user, amount, inter.guild.id)
        rps_challenge_text = translation_manager.get_text("gambling.rps_challenge", inter.user.id, inter.guild_id, target=user.mention, challenger=inter.user.mention, amount=amount)
        await inter.response.send_message(
            rps_challenge_text,
            view=view
        )
        view.msg = await inter.original_response()
    else:
        set_user_game(inter.guild.id, inter.user.id, True)
        view = RPSView(inter.user, None, amount, inter.guild.id)
        rps_vs_bot_text = translation_manager.get_text("gambling.rps_vs_bot", inter.user.id, inter.guild_id, amount=amount)
        await inter.response.send_message(
            rps_vs_bot_text,
            view=view, ephemeral=True
        )
        view.msg = await inter.original_response()

# Coinflip
class CFView(ui.View):
    def __init__(self, starter, target, bet, starter_side, guild_id):
        super().__init__(timeout=60)
        self.starter = starter
        self.target = target
        self.bet = bet
        self.starter_side = starter_side
        self.guild_id = guild_id
        self.msg = None
        
        # Set button labels with translations
        for item in self.children:
            if hasattr(item, 'callback') and item.callback.__name__ == 'accept':
                item.label = translation_manager.get_text("buttons.accept_bet", target.id, guild_id)
            elif hasattr(item, 'callback') and item.callback.__name__ == 'decline':
                item.label = translation_manager.get_text("buttons.decline_bet", target.id, guild_id)

    async def interaction_check(self, interaction):
        return interaction.user.id == self.target.id

    @ui.button(label="✅ Accept", style=ButtonStyle.success, row=0)
    async def accept(self, inter: discord.Interaction, button: ui.Button):
        await self._resolve(inter)

    @ui.button(label="❌ Decline", style=ButtonStyle.danger, row=0)
    async def decline(self, inter: discord.Interaction, button: ui.Button):
        eco_starter = get_user_eco(self.guild_id, self.starter.id)
        eco_target = get_user_eco(self.guild_id, self.target.id)
        eco_starter['balance'] += self.bet
        eco_target['balance'] += self.bet
        update_user_eco(self.guild_id, self.starter.id, eco_starter)
        update_user_eco(self.guild_id, self.target.id, eco_target)
        bet_rejected_text = translation_manager.get_text("gambling.bet_rejected", interaction.user.id, interaction.guild_id if interaction.guild else None)
        await self.msg.edit(embed=Embed(description=bet_rejected_text, color=0xFFA500), view=None)
        set_user_game(self.guild_id, self.starter.id, False)
        set_user_game(self.guild_id, self.target.id, False)
        self.stop()

    async def _resolve(self, inter):
        result = random.choice(['heads', 'tails'])
        eco_a = get_user_eco(self.guild_id, self.starter.id)
        eco_b = get_user_eco(self.guild_id, self.target.id)
        coinflip_result_text = translation_manager.get_text("gambling.coinflip_result", self.starter.id, self.guild_id, result=result.upper())
        if result == self.starter_side:
            eco_a['balance'] += self.bet * 2
            winner_text = translation_manager.get_text("gambling.coinflip_winner", self.starter.id, self.guild_id, winner=self.starter.mention, amount=self.bet*2)
            desc = f"{coinflip_result_text}\n{winner_text}"
            color = 0x22dd66
        else:
            eco_b['balance'] += self.bet * 2
            winner_text = translation_manager.get_text("gambling.coinflip_winner", self.target.id, self.guild_id, winner=self.target.mention, amount=self.bet*2)
            desc = f"{coinflip_result_text}\n{winner_text}"
            color = 0xdd2222
        
        update_user_eco(self.guild_id, self.starter.id, eco_a)
        update_user_eco(self.guild_id, self.target.id, eco_b)
        if result == self.starter_side:
            log_msg1 = translation_manager.get_text("logging.gambling_cf_win", None, None, winner=self.starter.mention, loser=self.target.mention, amount=self.bet)
            log_msg2 = translation_manager.get_text("logging.gambling_cf_loss", None, None, loser=self.target.mention, winner=self.starter.mention, amount=self.bet)
            log_econ(inter.guild.id, log_msg1, self.starter.id)
            log_econ(inter.guild.id, log_msg2, self.target.id)
        else:
            log_msg1 = translation_manager.get_text("logging.gambling_cf_win", None, None, winner=self.target.mention, loser=self.starter.mention, amount=self.bet)
            log_msg2 = translation_manager.get_text("logging.gambling_cf_loss", None, None, loser=self.starter.mention, winner=self.target.mention, amount=self.bet)
            log_econ(inter.guild.id, log_msg1, self.target.id)
            log_econ(inter.guild.id, log_msg2, self.starter.id)
        coinflip_title = translation_manager.get_text("gambling.coinflip_title", self.starter.id, self.guild_id)
        await self.msg.edit(embed=Embed(title=coinflip_title, description=desc, color=color), view=None)
        set_user_game(self.guild_id, self.starter.id, False)
        set_user_game(self.guild_id, self.target.id, False)
        self.stop()

    async def on_timeout(self):
        eco_starter = get_user_eco(self.guild_id, self.starter.id)
        eco_target = get_user_eco(self.guild_id, self.target.id)
        eco_starter['balance'] += self.bet
        eco_target['balance'] += self.bet
        update_user_eco(self.guild_id, self.starter.id, eco_starter)
        update_user_eco(self.guild_id, self.target.id, eco_target)
        try:
            bet_timeout_text = translation_manager.get_text("gambling.bet_timeout")
            await self.msg.edit(embed=Embed(description=bet_timeout_text, color=0xFFA500), view=None)
        except: pass
        set_user_game(self.guild_id, self.starter.id, False)
        set_user_game(self.guild_id, self.target.id, False)
        log_msg1 = translation_manager.get_text("logging.gambling_cf_timeout", None, None, user=self.starter.mention)
        log_msg2 = translation_manager.get_text("logging.gambling_cf_timeout", None, None, user=self.target.mention)
        log_econ(self.guild_id, log_msg1, self.starter.id)
        log_econ(self.guild_id, log_msg2, self.target.id)
        self.stop()

@app_commands.guild_only()
@bot.tree.command(name="cf", description=get_command_description("cf"))
@app_commands.describe(amount=get_parameter_description("amount"), side=get_parameter_description("side"), user=get_parameter_description("opponent"))
async def cf_cmd(inter: discord.Interaction, amount: int, side: str, user: Optional[discord.Member] = None):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if side not in ['heads','tails']:
        choose_side_text = translation_manager.get_text("gambling.choose_heads_tails", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(choose_side_text), ephemeral=True)
    eco = get_user_eco(inter.guild.id, inter.user.id)
    valid, msg = ensure_bet(eco, amount)
    if not valid:
        return await inter.response.send_message(embed=eco_error(msg), ephemeral=True)
    if is_user_in_game(inter.guild.id, inter.user.id):
        already_gambling_text = translation_manager.get_text("gambling.already_gambling", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(already_gambling_text), ephemeral=True)
    eco['balance'] -= amount
    update_user_eco(inter.guild.id, inter.user.id, eco)
    if user:
        if user.bot or user.id == inter.user.id:
            eco['balance'] += amount
            update_user_eco(inter.guild.id, inter.user.id, eco)
            invalid_opponent_text = translation_manager.get_text("gambling.invalid_opponent", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(invalid_opponent_text), ephemeral=True)
        eco2 = get_user_eco(inter.guild.id, user.id)
        valid2, msg2 = ensure_bet(eco2, amount)
        if not valid2:
            eco['balance'] += amount
            update_user_eco(inter.guild.id, inter.user.id, eco)
            other_player_no_money_text = translation_manager.get_text("gambling.other_player_no_money", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(other_player_no_money_text), ephemeral=True)
        if is_user_in_game(inter.guild.id, user.id):
            eco['balance'] += amount
            update_user_eco(inter.guild.id, inter.user.id, eco)
            other_player_gambling_text = translation_manager.get_text("gambling.other_player_gambling", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(other_player_gambling_text), ephemeral=True)
        eco2['balance'] -= amount
        update_user_eco(inter.guild.id, user.id, eco2)
        set_user_game(inter.guild.id, inter.user.id, True)
        set_user_game(inter.guild.id, user.id, True)
        view = CFView(inter.user, user, amount, side, inter.guild.id)
        coinflip_challenge_text = translation_manager.get_text("gambling.coinflip_challenge", inter.user.id, inter.guild_id, target=user.mention, challenger=inter.user.mention, amount=amount, side=side)
        await inter.response.send_message(coinflip_challenge_text, view=view)
        view.msg = await inter.original_response()
    else:
        result = random.choice(['heads','tails'])
        if result == side:
            win = amount*2
            eco['balance'] += win
            coinflip_title = translation_manager.get_text("gambling.coinflip_title", inter.user.id, inter.guild_id)
            coinflip_win_desc = translation_manager.get_text("gambling.coinflip_win", inter.user.id, inter.guild_id, result=result.upper(), amount=win//2)
            embed = Embed(title=coinflip_title, description=coinflip_win_desc, color=0x22dd66)
        else:
            coinflip_title = translation_manager.get_text("gambling.coinflip_title", inter.user.id, inter.guild_id)
            coinflip_lose_desc = translation_manager.get_text("gambling.coinflip_lose", inter.user.id, inter.guild_id, result=result.upper())
            embed = Embed(title=coinflip_title, description=coinflip_lose_desc, color=0xdd2222)
        update_user_eco(inter.guild.id, inter.user.id, eco)
        if result == side:
            log_msg = translation_manager.get_text("logging.gambling_cf_bot_win", None, None, user=inter.user.mention, amount=win//2)
            log_econ(inter.guild.id, log_msg, inter.user.id)
        else:
            log_msg = translation_manager.get_text("logging.gambling_cf_bot_loss", None, None, user=inter.user.mention, amount=amount)
            log_econ(inter.guild.id, log_msg, inter.user.id)
        await inter.response.send_message(embed=embed)
        set_user_game(inter.guild.id, inter.user.id, False)

# Roulette
class RouletteView(ui.View):
    def __init__(self, user, bet, guild_id):
        super().__init__(timeout=30)
        self.user = user
        self.bet = bet
        self.guild_id = guild_id
        self.clicked = False
        self.msg = None

    async def interaction_check(self, interaction):
        return interaction.user.id == self.user.id

    @ui.button(label="🟥 RED", style=ButtonStyle.danger, row=0)
    async def red(self, interaction: discord.Interaction, button: ui.Button):
        await self.spin(interaction, "red")

    @ui.button(label="⬛ BLACK", style=ButtonStyle.primary, row=0)
    async def black(self, interaction: discord.Interaction, button: ui.Button):
        await self.spin(interaction, "black")

    @ui.button(label="🟩 GREEN", style=ButtonStyle.success, row=0)
    async def green(self, interaction: discord.Interaction, button: ui.Button):
        await self.spin(interaction, "green")

    async def spin(self, inter, color):
        if self.clicked:
            return
        self.clicked = True
        wheel = ["⬛", "🟥", "🟥", "⬛", "🟩", "⬛", "🟥", "⬛", "⬛", "🟥", "⬛", "🟥", "🟥", "⬛", "🟥"]
        spin_idx = random.randint(0, len(wheel) - 1)
        spin_seq = []
        for i in range(spin_idx + 5, spin_idx + 11):
            seq = " ".join(wheel[j % len(wheel)] for j in range(i, i + 7))
            spin_seq.append(seq)
        for frame in spin_seq[:-1]:
            await asyncio.sleep(0.3)
            await self.msg.edit(content=f"🎰 | {frame}", embed=None, view=self)
        await asyncio.sleep(0.5)
        win_color = "red" if wheel[spin_idx] == "🟥" else ("black" if wheel[spin_idx] == "⬛" else "green")
        win = False
        mult = 2 if color != "green" else 10
        eco = get_user_eco(self.guild_id, self.user.id)
        if color == "green":
            win = (win_color == "green")
        else:
            win = (win_color == color)
        if win:
            reward = self.bet * mult
            eco['balance'] += reward
            roulette_title = translation_manager.get_text("gambling.roulette_title", None, None)
            roulette_win_text = translation_manager.get_text("gambling.roulette_desc_win", None, None, color=win_color.upper(), amount=reward - self.bet, result=wheel[spin_idx])
            embed = Embed(title=roulette_title, description=roulette_win_text, color=0x22dd66)
        else:
            lost = int(self.bet * 0.9)
            eco['balance'] += self.bet - lost
            roulette_title = translation_manager.get_text("gambling.roulette_title", None, None)
            roulette_lose_text = translation_manager.get_text("gambling.roulette_desc_lose", None, None, color=win_color.upper(), amount=lost, result=wheel[spin_idx])
            embed = Embed(title=roulette_title, description=roulette_lose_text, color=0xdd2222)
        update_user_eco(self.guild_id, self.user.id, eco)
        if win:
            log_msg = translation_manager.get_text("logging.gambling_roulette_win", None, None, user=self.user.mention, color=color, amount=reward - self.bet)
            log_econ(self.guild_id, log_msg, self.user.id)
        else:
            log_msg = translation_manager.get_text("logging.gambling_roulette_loss", None, None, user=self.user.mention, color=color, amount=lost)
            log_econ(self.guild_id, log_msg, self.user.id)
        
        await self.msg.edit(content=None, embed=embed, view=None)
        set_user_game(self.guild_id, self.user.id, False)
        self.stop()

    async def on_timeout(self):
        if self.clicked:
            return
        eco = get_user_eco(self.guild_id, self.user.id)
        eco['balance'] += self.bet
        update_user_eco(self.guild_id, self.user.id, eco)
        log_msg = translation_manager.get_text("logging.gambling_roulette_timeout", None, None, user=self.user.mention)
        log_econ(self.guild_id, log_msg, self.user.id)
        try:
            roulette_timeout_text = translation_manager.get_text("gambling.roulette_timeout", self.user.id, self.guild_id)
            await self.msg.edit(content=roulette_timeout_text, embed=None, view=None)
        except:
            pass
        set_user_game(self.guild_id, self.user.id, False)
        self.stop()

@app_commands.guild_only()
@bot.tree.command(name="roulette", description=get_command_description("roulette"))
@app_commands.describe(amount=get_parameter_description("amount"))
async def roulette_cmd(inter: discord.Interaction, amount: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    eco = get_user_eco(inter.guild.id, inter.user.id)
    valid, msg = ensure_bet(eco, amount)
    if not valid:
        return await inter.response.send_message(embed=eco_error(msg), ephemeral=True)
    if is_user_in_game(inter.guild.id, inter.user.id):
        already_gambling_text = translation_manager.get_text("gambling.already_gambling", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(already_gambling_text), ephemeral=True)
    eco['balance'] -= amount
    update_user_eco(inter.guild.id, inter.user.id, eco)
    set_user_game(inter.guild.id, inter.user.id, True)
    view = RouletteView(inter.user, amount, inter.guild.id)
    roulette_choose_color_text = translation_manager.get_text("gambling.roulette_choose_color", inter.user.id, inter.guild_id)
    await inter.response.send_message(
        roulette_choose_color_text, view=view, ephemeral=True
    )
    view.msg = await inter.original_response()

class BlackjackView(ui.View):
    def __init__(self, player, bet, guild_id):
        super().__init__(timeout=60)
        self.player = player
        self.bet = bet
        self.guild_id = guild_id
        self.deck = [2,3,4,5,6,7,8,9,10,10,10,10,11]*4
        random.shuffle(self.deck)
        self.p_hand = [self.deck.pop(), self.deck.pop()]
        self.d_hand = [self.deck.pop(), self.deck.pop()]
        self.msg = None
        self.stopped = False

    def value(self, hand):
        total = sum(hand)
        aces = hand.count(11)
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    @ui.button(label="🃏 HIT", style=ButtonStyle.primary, row=0)
    async def hit(self, inter: discord.Interaction, button: ui.Button):
        if self.stopped: return
        self.p_hand.append(self.deck.pop())
        pv = self.value(self.p_hand)
        if pv > 21:
            bust_text = translation_manager.get_text("gambling.blackjack_bust", inter.user.id, inter.guild_id)
            await self.end_game(inter, bust_text, False)
        else:
            await self.update_msg(inter)

    @ui.button(label="✋ STAND", style=ButtonStyle.success, row=0)
    async def stand(self, inter: discord.Interaction, button: ui.Button):
        if self.stopped: return
        while self.value(self.d_hand) < 17:
            self.d_hand.append(self.deck.pop())
        await self.finish(inter)

    async def finish(self, inter):
        pv = self.value(self.p_hand)
        dv = self.value(self.d_hand)
        eco = get_user_eco(self.guild_id, self.player.id)
        if dv > 21 or pv > dv:
            win = self.bet * 3
            eco['balance'] += win
            text = translation_manager.get_text("gambling.blackjack_win_text", self.player.id, self.guild_id,
                                              player_hand=self.p_hand, player_value=pv,
                                              dealer_hand=self.d_hand, dealer_value=dv,
                                              winnings=win - self.bet)
            color = 0x22dd66
        elif pv == dv:
            eco['balance'] += self.bet
            text = translation_manager.get_text("gambling.blackjack_tie_text", self.player.id, self.guild_id,
                                              player_hand=self.p_hand, player_value=pv,
                                              dealer_hand=self.d_hand, dealer_value=dv)
            color = 0xFFA500
        else:
            eco['balance'] += int(self.bet * 0.25)
            loss = int(self.bet * 0.75)
            text = translation_manager.get_text("gambling.blackjack_lose_text", self.player.id, self.guild_id,
                                              player_hand=self.p_hand, player_value=pv,
                                              dealer_hand=self.d_hand, dealer_value=dv, loss=loss)
            color = 0xdd2222
        update_user_eco(self.guild_id, self.player.id, eco)
        if dv > 21 or pv > dv:
            log_text = translation_manager.get_text("economy.blackjack_win_log", inter.user.id, inter.guild_id,
                                                   user=inter.user.mention, amount=win - self.bet)
            log_econ(inter.guild.id, log_text, inter.user.id)
        elif pv == dv:
            log_text = translation_manager.get_text("economy.blackjack_tie_log", inter.user.id, inter.guild_id,
                                                   user=inter.user.mention)
            log_econ(inter.guild.id, log_text, inter.user.id)
        else:
            log_text = translation_manager.get_text("economy.blackjack_lose_log", inter.user.id, inter.guild_id,
                                                   user=inter.user.mention, amount=loss)
            log_econ(inter.guild.id, log_text, inter.user.id)
        blackjack_title = translation_manager.get_text("gambling.blackjack_title", self.player.id, self.guild_id)
        await self.msg.edit(embed=Embed(title=blackjack_title, description=text, color=color), view=None)
        set_user_game(self.guild_id, self.player.id, False)
        self.stopped = True
        self.stop()

    async def end_game(self, inter, msg, win):
        eco = get_user_eco(self.guild_id, self.player.id)
        if win:
            winnings = self.bet * 2
            eco['balance'] += winnings
            win_text = translation_manager.get_text("gambling.blackjack_win_winnings", self.player.id, self.guild_id, winnings=winnings)
            text = msg + "\n" + win_text
            color = 0x22dd66
        else:
            eco['balance'] += int(self.bet * 0.25)
            loss = int(self.bet * 0.75)
            lose_text = translation_manager.get_text("gambling.blackjack_lose_loss", self.player.id, self.guild_id, loss=loss)
            text = msg + "\n" + lose_text
            color = 0xdd2222
        update_user_eco(self.guild_id, self.player.id, eco)
        if win:
            log_text = translation_manager.get_text("economy.blackjack_win_log", inter.user.id, inter.guild_id,
                                                   user=inter.user.mention, amount=winnings)
            log_econ(inter.guild.id, log_text, inter.user.id)
        else:
            log_text = translation_manager.get_text("economy.blackjack_lose_log", inter.user.id, inter.guild_id,
                                                   user=inter.user.mention, amount=loss)
            log_econ(inter.guild.id, log_text, inter.user.id)
        blackjack_title = translation_manager.get_text("gambling.blackjack_title", self.player.id, self.guild_id)
        await self.msg.edit(embed=Embed(title=blackjack_title, description=text, color=color), view=None)
        set_user_game(self.guild_id, self.player.id, False)
        self.stopped = True
        self.stop()

    async def update_msg(self, inter):
        pv = self.value(self.p_hand)
        dv = self.d_hand[0]
        blackjack_title = translation_manager.get_text("gambling.blackjack_title", self.player.id, self.guild_id)
        embed = Embed(
            title=blackjack_title,
            description=translation_manager.get_text("gambling.blackjack_cards", hand=self.p_hand, value=pv, dealer=dv),
            color=0x3366ff
        )
        await self.msg.edit(embed=embed, view=self)

    async def on_timeout(self):
        try:
            timeout_text = translation_manager.get_text("gambling.blackjack_timeout")
            await self.msg.edit(embed=Embed(description=timeout_text, color=0xFFA500), view=None)
            eco = get_user_eco(self.guild_id, self.player.id)
            eco['balance'] += int(self.bet * 0.5)
            loss = int(self.bet * 0.5)
            update_user_eco(self.guild_id, self.player.id, eco)
            log_text = translation_manager.get_text("economy.blackjack_timeout_log", self.player.id, self.guild.id,
                                                   user=self.player.mention, amount=loss)
            log_econ(self.guild.id, log_text, self.player.id)
        except:
            pass
        set_user_game(self.guild_id, self.player.id, False)
        self.stopped = True
        self.stop()

@app_commands.guild_only()
@bot.tree.command(name="blackjack", description=get_command_description("blackjack"))
@app_commands.describe(amount=get_parameter_description("amount"))
async def blackjack_cmd(inter: discord.Interaction, amount: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    eco = get_user_eco(inter.guild.id, inter.user.id)
    valid, msg = ensure_bet(eco, amount)
    if not valid:
        return await inter.response.send_message(embed=eco_error(msg), ephemeral=True)
    if is_user_in_game(inter.guild.id, inter.user.id):
        already_gambling_text = translation_manager.get_text("gambling.already_gambling", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(already_gambling_text), ephemeral=True)
    eco['balance'] -= amount
    update_user_eco(inter.guild.id, inter.user.id, eco)
    set_user_game(inter.guild.id, inter.user.id, True)
    view = BlackjackView(inter.user, amount, inter.guild.id)
    blackjack_title = translation_manager.get_text("gambling.blackjack_title", inter.user.id, inter.guild_id)
    await inter.response.send_message(embed=Embed(
        title=blackjack_title,
        description=translation_manager.get_text("gambling.blackjack_desc", inter.user.id, inter.guild_id),
        color=0x3366ff
    ), view=view, ephemeral=True)
    view.msg = await inter.original_response()

# Mines game
class MinesFieldButton(ui.Button):
    def __init__(self, idx):
        super().__init__(label="⬜", style=ButtonStyle.secondary, row=idx // 5, custom_id=f"mines_{idx}")
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        view: MinesView = self.view
        if view.stopped:
            game_ended_text = translation_manager.get_text("gambling.game_ended", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(game_ended_text, ephemeral=True)
            return

        if self.idx in view.revealed:
            if len(view.revealed) == 0:
                must_reveal_text = translation_manager.get_text("gambling.must_reveal_one_field", interaction.user.id, interaction.guild_id)
                await interaction.response.send_message(must_reveal_text, ephemeral=True)
                return
            await view.cashout(interaction)
            return

        if view.board[self.idx] == 1:
            await view.lose(interaction, hit_idx=self.idx)
            return

        view.revealed.add(self.idx)
        self.label = "✅"
        self.style = ButtonStyle.success

        if len(view.revealed) == 25 - view.minecount:
            view.revealed.update(i for i in range(25) if view.board[i] == 0)
            await view.win(interaction)
            return

        mult = view.get_multiplier()
        msg = translation_manager.get_text("messages.mines_progress", view.user.id, view.guild_id, revealed=len(view.revealed), total=25 - view.minecount, multiplier=mult)
        mines_title = translation_manager.get_text("gambling.mines_title", view.user.id, view.guild_id)
        await interaction.response.edit_message(embed=Embed(title=mines_title, description=msg, color=0x3366ff), view=view)

class MinesView(ui.View):
    def __init__(self, user, bet, minecount, guild_id):
        super().__init__(timeout=120)
        self.user = user
        self.bet = bet
        self.minecount = minecount
        self.guild_id = guild_id
        self.board = [0]*25
        for i in random.sample(range(25), minecount):
            self.board[i] = 1
        self.revealed = set()
        self.stopped = False
        self.msg = None
        for i in range(25):
            self.add_item(MinesFieldButton(i))

    async def interaction_check(self, interaction):
        return interaction.user.id == self.user.id

    def get_multiplier(self):
        safe = len(self.revealed)
        max_safe = 25 - self.minecount
        if self.minecount <= 5:
            return round(1 + 1 * (safe/max_safe), 2)
        elif self.minecount <= 10:
            return round(1 + 2 * (safe/max_safe), 2)
        elif self.minecount <= 15:
            return round(1 + 3 * (safe/max_safe), 2)
        else:
            return round(1 + 4 * (safe/max_safe), 2)

    async def win(self, interaction):
        self.stopped = True
        set_user_game(self.guild_id, self.user.id, False)
        eco = get_user_eco(self.guild_id, self.user.id)
        win = int(self.bet * self.get_multiplier())
        eco['balance'] += win
        update_user_eco(self.guild_id, self.user.id, eco)
        board_render = []
        for i in range(25):
            if self.board[i] == 1:
                board_render.append("💣")
            elif i in self.revealed:
                board_render.append("✅")
            else:
                board_render.append("⬜")
        board = "\n".join("".join(board_render[i*5:i*5+5]) for i in range(5))
        for idx, item in enumerate(self.children):
            if not isinstance(item, MinesFieldButton): continue
            item.disabled = True
            if self.board[idx] == 1:
                item.label = "💣"
                item.style = ButtonStyle.secondary
            elif idx in self.revealed:
                item.label = "✅"
                item.style = ButtonStyle.success
            else:
                item.label = "⬜"
                item.style = ButtonStyle.secondary
        await interaction.response.edit_message(embed=Embed(
            title=translation_manager.get_text("gambling.mines_win_title", self.user.id, self.guild_id),
            description=translation_manager.get_text("gambling.mines_win_all", amount=win - self.bet, multiplier=self.get_multiplier(), board=board),
            color=0x22dd66
        ), view=self)
        log_econ(self.guild_id, translation_manager.get_text("logs.mines_played_won", self.user.id, self.guild_id, user=self.user.mention, amount=win - self.bet, mines=self.minecount), self.user.id)
        self.stop()

    async def cashout(self, interaction):
        self.stopped = True
        set_user_game(self.guild_id, self.user.id, False)
        eco = get_user_eco(self.guild_id, self.user.id)
        win = int(self.bet * self.get_multiplier())
        eco['balance'] += win
        update_user_eco(self.guild_id, self.user.id, eco)
        board_render = []
        for i in range(25):
            if self.board[i] == 1:
                board_render.append("💣")
            elif i in self.revealed:
                board_render.append("✅")
            else:
                board_render.append("⬜")
        board = "\n".join("".join(board_render[i*5:i*5+5]) for i in range(5))
        for idx, item in enumerate(self.children):
            if not isinstance(item, MinesFieldButton): continue
            item.disabled = True
            if self.board[idx] == 1:
                item.label = "💣"
                item.style = ButtonStyle.secondary
            elif idx in self.revealed:
                item.label = "✅"
                item.style = ButtonStyle.success
            else:
                item.label = "⬜"
                item.style = ButtonStyle.secondary
        await interaction.response.edit_message(embed=Embed(
            title=translation_manager.get_text("gambling.mines_win_title", self.user.id, self.guild_id),
            description=translation_manager.get_text("gambling.mines_win_partial", amount=win - self.bet, revealed=len(self.revealed), multiplier=self.get_multiplier(), board=board),
            color=0x22dd66
        ), view=self)
        log_econ(self.guild_id, translation_manager.get_text("logs.mines_played_won", self.user.id, self.guild_id, user=self.user.mention, amount=win - self.bet, mines=self.minecount), self.user.id)
        self.stop()

    async def lose(self, interaction, hit_idx=None):
        self.stopped = True
        set_user_game(self.guild_id, self.user.id, False)
        board_render = []
        for i in range(25):
            if self.board[i] == 1:
                if i == hit_idx:
                    board_render.append("💥")
                else:
                    board_render.append("💣")
            elif i in self.revealed:
                board_render.append("✅")
            else:
                board_render.append("⬜")
        board = "\n".join("".join(board_render[i*5:i*5+5]) for i in range(5))
        for idx, item in enumerate(self.children):
            if not isinstance(item, MinesFieldButton): continue
            item.disabled = True
            if self.board[idx] == 1:
                if idx == hit_idx:
                    item.label = "💥"
                    item.style = ButtonStyle.danger
                else:
                    item.label = "💣"
                    item.style = ButtonStyle.secondary
            elif idx in self.revealed:
                item.label = "✅"
                item.style = ButtonStyle.success
            else:
                item.label = "⬜"
                item.style = ButtonStyle.secondary
        await interaction.response.edit_message(embed=Embed(
            title=translation_manager.get_text("gambling.mines_lose_title", self.user.id, self.guild_id),
            description=translation_manager.get_text("gambling.mines_lose", board=board, amount=self.bet),
            color=0xdd2222
        ), view=self)
        log_econ(self.guild_id, translation_manager.get_text("logs.mines_played_lost", self.user.id, self.guild_id, user=self.user.mention, amount=self.bet, mines=self.minecount), self.user.id)
        self.stop()

    async def on_timeout(self):
        if self.stopped: return
        eco = get_user_eco(self.guild_id, self.user.id)
        eco['balance'] += int(self.bet * 0.5)
        loss = int(self.bet * 0.5)
        update_user_eco(self.guild_id, self.user.id, eco)
        for item in self.children:
            item.disabled = True
            if isinstance(item, MinesFieldButton):
                if self.board[item.idx] == 1:
                    item.label = "💣"
                    item.style = ButtonStyle.secondary
                elif item.idx in self.revealed:
                    item.label = "✅"
                    item.style = ButtonStyle.success
                else:
                    item.label = "⬜"
                    item.style = ButtonStyle.secondary
        try:
            timeout_text = translation_manager.get_text("gambling.mines_timeout", amount=loss)
            await self.msg.edit(embed=Embed(description=timeout_text, color=0xFFA500), view=self)
        except:
            pass
        log_econ(self.guild_id, translation_manager.get_text("logs.mines_timeout", self.user.id, self.guild_id, user=self.user.mention, amount=loss), self.user.id)
        set_user_game(self.guild_id, self.user.id, False)
        self.stopped = True
        self.stop()

@app_commands.guild_only()
@bot.tree.command(name="mines", description=get_command_description("mines"))
@app_commands.describe(amount=get_parameter_description("amount"), mines=get_parameter_description("mines"))
async def mines_cmd(inter: discord.Interaction, amount: int, mines: int):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not (1 <= mines <= 20):
        mines_range_text = translation_manager.get_text("gambling.mines_range", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(mines_range_text), ephemeral=True)
    eco = get_user_eco(inter.guild.id, inter.user.id)
    valid, msg = ensure_bet(eco, amount)
    if not valid:
        return await inter.response.send_message(embed=eco_error(msg), ephemeral=True)
    if is_user_in_game(inter.guild.id, inter.user.id):
        already_gambling_text = translation_manager.get_text("gambling.already_gambling", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(already_gambling_text), ephemeral=True)
    eco['balance'] -= amount
    update_user_eco(inter.guild.id, inter.user.id, eco)
    set_user_game(inter.guild.id, inter.user.id, True)
    view = MinesView(inter.user, amount, mines, inter.guild.id)
    await inter.response.send_message(embed=Embed(
        title=translation_manager.get_text("gambling.mines_title", inter.user.id, inter.guild_id),
        description=translation_manager.get_text("gambling.mines_desc", inter.user.id, inter.guild_id, mines=mines),
        color=0x3366ff
    ), view=view, ephemeral=True)
    view.msg = await inter.original_response()

# Shop system
def get_shop_path(guild_id):
    os.makedirs('economy', exist_ok=True)
    return f'economy/{guild_id}_shop.json'

def load_shop(guild_id):
    path = get_shop_path(guild_id)
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_shop(guild_id, data):
    path = get_shop_path(guild_id)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

class ShopItem(app_commands.Choice):
    def __init__(self, name, value):
        super().__init__(name=name, value=value)

@app_commands.guild_only()
@bot.tree.command(name="admin_shopadd", description=get_command_description("admin_shopadd"))
@app_commands.describe(role=get_parameter_description("role_to_buy"), price=get_parameter_description("price"), alias=get_parameter_description("alias"))
@app_commands.checks.has_permissions(administrator=True)
async def shopadd_cmd(inter: discord.Interaction, role: discord.Role, price: int, alias: str):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not inter.user.guild_permissions.administrator:
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    shop = load_shop(inter.guild.id)
    alias = alias.lower()
    if alias in shop:
        alias_exists_text = translation_manager.get_text("economy.alias_exists", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(alias_exists_text), ephemeral=True)
    for item in shop.values():
        if item['role_id'] == role.id:
            role_in_shop_text = translation_manager.get_text("economy.role_in_shop", inter.user.id, inter.guild_id)
            return await inter.response.send_message(embed=eco_error(role_in_shop_text), ephemeral=True)
    shop[alias] = {
        "role_id": role.id,
        "price": price,
        "name": role.name
    }
    save_shop(inter.guild.id, shop)
    shop_role_added_text = translation_manager.get_text("economy.shop_role_added", inter.user.id, inter.guild_id, role=role.mention, price=price, alias=alias)
    await inter.response.send_message(embed=eco_success(shop_role_added_text), ephemeral=True)
    log_econ(inter.guild.id, translation_manager.get_text("logs.admin_shop_add", inter.user.id, inter.guild_id, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", role=role.name, price=price, alias=alias), inter.user.id)

@app_commands.guild_only()
@bot.tree.command(name="admin_shopremove", description=get_command_description("admin_shopremove"))
@app_commands.describe(alias=get_parameter_description("alias_remove"))
@app_commands.checks.has_permissions(administrator=True)
async def shopremove_cmd(inter: discord.Interaction, alias: str):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not inter.user.guild_permissions.administrator:
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    shop = load_shop(inter.guild.id)
    alias = alias.lower()
    if alias not in shop:
        alias_not_found_text = translation_manager.get_text("economy.alias_not_found", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(alias_not_found_text), ephemeral=True)
    item = shop.pop(alias)
    save_shop(inter.guild.id, shop)
    role = inter.guild.get_role(item['role_id'])
    shop_role_removed_text = translation_manager.get_text("economy.shop_role_removed", inter.user.id, inter.guild_id, role=role.mention if role else item['name'], alias=alias)
    await inter.response.send_message(embed=eco_success(shop_role_removed_text), ephemeral=True)
    log_econ(inter.guild.id, translation_manager.get_text("logs.admin_shop_remove", inter.user.id, inter.guild_id, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", role=item['name'], alias=alias), inter.user.id)

@app_commands.guild_only()
@bot.tree.command(name="admin_shoplist", description=get_command_description("admin_shoplist"))
@app_commands.checks.has_permissions(administrator=True)
async def shoplist_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not inter.user.guild_permissions.administrator:
        no_perms = translation_manager.get_text("general.no_permissions", inter.user.id, inter.guild.id)
        return await inter.response.send_message(embed=eco_error(no_perms), ephemeral=True)
    shop = load_shop(inter.guild.id)
    if not shop:
        shop_empty_text = translation_manager.get_text("economy.shop_empty_description", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(shop_empty_text), ephemeral=True)
    shop_title = translation_manager.get_text("economy.shop_title", inter.user.id, inter.guild.id)
    embed = Embed(title=shop_title, color=0x22dd66)
    for alias, item in shop.items():
        role = inter.guild.get_role(item['role_id'])
        price_label = translation_manager.get_text("economy.price_label", inter.user.id, inter.guild_id)
        embed.add_field(
            name=f"{role.mention if role else item['name']} (`{alias}`)",
            value=f"{price_label} **{item['price']}$**",
            inline=False
        )
    await inter.response.send_message(embed=embed, ephemeral=True)

class BuyAliasAutocomplete(app_commands.Transform):
    @classmethod
    async def transform(cls, interaction: discord.Interaction, value: str) -> str:
        return value.lower()

    @classmethod
    async def autocomplete(cls, interaction: discord.Interaction, current: str):
        shop = load_shop(interaction.guild.id)
        return [
            app_commands.Choice(name=f" {alias} ({item['name']})", value=alias)
            for alias, item in shop.items()
            if current.lower() in alias or current.lower() in item['name'].lower()
        ][:20]

@app_commands.guild_only()
@bot.tree.command(name="buy", description=get_command_description("buy"))
@app_commands.describe(alias=get_parameter_description("alias_buy"))
@app_commands.autocomplete(alias=BuyAliasAutocomplete.autocomplete)
async def buy_cmd(inter: discord.Interaction, alias: str):
    if not await check_changelog_and_module(inter, "economy"):
        return
    shop = load_shop(inter.guild.id)
    alias = alias.lower()
    if alias not in shop:
        alias_not_found_text = translation_manager.get_text("economy.alias_not_found", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(alias_not_found_text), ephemeral=True)
    item = shop[alias]
    eco = get_user_eco(inter.guild.id, inter.user.id)
    if eco['balance'] < item['price']:
        not_enough_money_text = translation_manager.get_text("economy.not_enough_money", inter.user.id, inter.guild_id, price=item['price'])
        return await inter.response.send_message(embed=eco_error(not_enough_money_text), ephemeral=True)
    role = inter.guild.get_role(item['role_id'])
    if not role:
        role_deleted_text = translation_manager.get_text("economy.role_deleted", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(role_deleted_text), ephemeral=True)
    if role in inter.user.roles:
        already_have_role_text = translation_manager.get_text("economy.already_have_role", inter.user.id, inter.guild_id)
        return await inter.response.send_message(embed=eco_error(already_have_role_text), ephemeral=True)
    eco['balance'] -= item['price']
    update_user_eco(inter.guild.id, inter.user.id, eco)
    await inter.user.add_roles(role, reason=translation_manager.get_text("messages.role_purchased", inter.user.id, inter.guild_id))
    role_purchased_text = translation_manager.get_text("economy.role_purchased", inter.user.id, inter.guild_id, role=role.mention, price=item['price'])
    await inter.response.send_message(embed=eco_success(role_purchased_text))
    purchase_log = translation_manager.get_text("logging.role_purchase", None, None, username=inter.user.name, display_name=inter.user.display_name, user_id=inter.user.id, role_name=role.name, price=item['price'], alias=alias)
    log_econ(inter.guild.id, purchase_log, inter.user.id)

@app_commands.guild_only()
@bot.tree.command(name="shop", description=get_command_description("shop"))
async def shop_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    shop = load_shop(inter.guild.id)
    if not shop:
        shop_empty_title = translation_manager.get_text("economy.shop_empty_title", inter.user.id, inter.guild.id)
        shop_empty_description = translation_manager.get_text("economy.shop_empty_description", inter.user.id, inter.guild.id)
        return await inter.response.send_message(
            embed=Embed(title=shop_empty_title, description=shop_empty_description, color=0xff2222),
            ephemeral=True
        )
    desc = ""
    sorted_items = sorted(shop.items(), key=lambda x: x[1]['price'])
    for alias, item in sorted_items:
        role = inter.guild.get_role(item['role_id'])
        if role:
            desc += f"**{role.mention}**\nCena: `{item['price']}$` • Alias: `{alias}`\n\n"
        else:
            desc += translation_manager.get_text("messages.role_deleted_shop", inter.user.id, inter.guild_id, name=item['name'], price=item['price'], alias=alias)
    role_descriptions_text = translation_manager.get_text("economy.role_descriptions", inter.user.id, inter.guild.id)
    desc += f"\n {role_descriptions_text}"
    shop_title = translation_manager.get_text("economy.shop_title", inter.user.id, inter.guild.id)
    await inter.response.send_message(
        embed=Embed(
            title=shop_title,
            description=desc,
            color=0x3388ff
        ),
        ephemeral=True
    )

# ------- RESET EKONOMII ------

def reset_econ_data(guild_id):
    gid = str(guild_id)
    paths = [
        f"economy/{gid}.json",
        f"economy/{gid}_logs.json",
        f"economy/{gid}_shop.json"
    ]
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

class EconResetConfirm(ui.View):
    def __init__(self, author, guild_id):
        super().__init__(timeout=30)
        self.author = author
        self.guild_id = guild_id
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.author.id

    @ui.button(label="❗ Reset Economy", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        reset_econ_data(self.guild_id)
        log_econ(self.guild_id, translation_manager.get_text("logs.economy_reset", self.author.id, self.guild_id, admin=f"{self.author.name} ({self.author.display_name}) [{self.author.id}]"))
        economy_reset_text = translation_manager.get_text("economy.economy_reset", interaction.user.id, interaction.guild_id)
        await interaction.response.edit_message(
            content=economy_reset_text, view=None
        )
        self.stop()

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        economy_reset_cancelled_text = translation_manager.get_text("economy.economy_reset_cancelled", interaction.user.id, interaction.guild_id)
        await interaction.response.edit_message(
            content=economy_reset_cancelled_text, view=None
        )
        self.stop()

@app_commands.guild_only()
@bot.tree.command(name="resetecon", description=get_command_description("resetecon"))
@app_commands.default_permissions(administrator=True)
async def resetecon_cmd(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    view = EconResetConfirm(inter.user, inter.guild.id)
    await inter.response.send_message(
        translation_manager.get_text("messages.economy_reset_warning", inter.user.id, inter.guild_id),
        view=view, ephemeral=True
    )

@app_commands.guild_only()
@bot.tree.command(name="resetgamestates", description=get_command_description("resetgamestates"))
async def resetgamestates(inter: discord.Interaction):
    if not await check_changelog_and_module(inter, "economy"):
        return
    if not inter.user.guild_permissions.administrator:
        return await inter.response.send_message(
            translation_manager.get_text("errors.admin_only_command", inter.user.id, inter.guild_id),
            ephemeral=True
        )
    guild_id = str(inter.guild.id)
    econ_path = os.path.join("economy", f"{guild_id}.json")
    if not os.path.exists(econ_path):
        return await inter.response.send_message(
            translation_manager.get_text("messages.no_economy_data", inter.user.id, inter.guild_id), ephemeral=True
        )
    with open(econ_path, "r") as f:
        data = json.load(f)
    users = data.get("users", {})
    reset_count = 0
    for user_id, user_data in users.items():
        if user_data.get("in_game"):
            user_data["in_game"] = False
            reset_count += 1
    with open(econ_path, "w") as f:
        json.dump(data, f)
    log_econ(inter.guild.id, translation_manager.get_text("logs.gamestates_reset", inter.user.id, inter.guild_id, admin=f"{inter.user.name} ({inter.user.display_name}) [{inter.user.id}]", count=reset_count))
    await inter.response.send_message(
        translation_manager.get_text("messages.gamestates_reset_success", inter.user.id, inter.guild_id, count=reset_count),
        ephemeral=True
    )
    

    # —– RR CREATE —–
@app_commands.guild_only()
@bot.tree.command(name="rrcreate", description=get_command_description("rrcreate"))
@app_commands.describe(
    message_id="Message ID (text)",
    emoji="Emoji (e.g. 🔥 or <:custom:123…>)",
    role="Role to assign",
    removable="Whether removable (true/false)?"
)
async def rrcreate(
    interaction: discord.Interaction,
    message_id: str,
    emoji: str,
    role: discord.Role,
    removable: bool = True
):
    if not await check_changelog_and_module(interaction, "moderation"):
        return
    if not interaction.user.guild_permissions.administrator:
        no_perms = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(f"🚫 {no_perms}", ephemeral=True)

    try:
        msg_id = int(message_id)
    except ValueError:
        invalid_msg_id = translation_manager.get_text("general.invalid_message_id", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(f"❌ {invalid_msg_id}", ephemeral=True)
    channel_id = interaction.channel.id

    data = load_reaction_roles(interaction.guild.id)
    entry = {"emoji": emoji, "role_id": role.id, "removable": removable}
    key = str(msg_id)
    if key not in data:
        data[key] = {"channel_id": channel_id, "reactions": [entry]}
    else:
        data[key]["reactions"].append(entry)
    save_reaction_roles(interaction.guild.id, data)

    try:
        channel = interaction.guild.get_channel(channel_id)
        msg = await channel.fetch_message(msg_id)
        await msg.add_reaction(emoji)
    except:
        pass

    rr_created = translation_manager.get_text("reaction_roles.created", interaction.user.id, interaction.guild_id, emoji=emoji, role_id=role.id, removable=removable)
    await interaction.response.send_message(f"✅ {rr_created}", ephemeral=True)


# —– RR LIST —–
@app_commands.guild_only()
@bot.tree.command(name="rrlist", description=get_command_description("rrlist"))
async def rrlist(interaction: discord.Interaction):
    if not await check_changelog_and_module(interaction, "moderation"):
        return
    if not interaction.user.guild_permissions.administrator:
        no_perms = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(f"🚫 {no_perms}", ephemeral=True)

    data = load_reaction_roles(interaction.guild.id)
    if not data:
        no_rr = translation_manager.get_text("reaction_roles.none", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(no_rr, ephemeral=True)

    title_text = translation_manager.get_text("reaction_roles.title", interaction.user.id, interaction.guild_id)
    embed = discord.Embed(title=title_text, color=discord.Color.blue())
    for msg_id, info in data.items():
        chan_id = info["channel_id"]
        lines = []
        for r in info["reactions"]:
            role = interaction.guild.get_role(r["role_id"])
            lines.append(f"{r['emoji']} → {role.mention if role else '??'} (removable={r['removable']})")
        field_name = translation_manager.get_text("reaction_roles.message_field", interaction.user.id, interaction.guild_id, msg_id=msg_id, chan_id=chan_id)
        embed.add_field(
            name=field_name,
            value="\n".join(lines),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# —– RR DELETE —–
@app_commands.guild_only()
@bot.tree.command(name="rrdelete", description=get_command_description("rrdelete"))
@app_commands.describe(message_id=get_parameter_description("message_id"))
async def rrdelete(
    interaction: discord.Interaction,
    message_id: str
):
    if not await check_changelog_and_module(interaction, "moderation"):
        return
    if not interaction.user.guild_permissions.administrator:
        no_perms = translation_manager.get_text("general.no_permissions", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(f"🚫 {no_perms}", ephemeral=True)

    try:
        msg_id = int(message_id)
    except ValueError:
        invalid_msg_id = translation_manager.get_text("general.invalid_message_id", interaction.user.id, interaction.guild_id)
        return await interaction.response.send_message(f"❌ {invalid_msg_id}", ephemeral=True)

    data = load_reaction_roles(interaction.guild.id)
    key = str(msg_id)
    if key in data:
        info = data.pop(key)
        save_reaction_roles(interaction.guild.id, data)
        try:
            channel = interaction.guild.get_channel(info["channel_id"])
            msg = await channel.fetch_message(msg_id)
            await msg.clear_reactions()
        except:
            pass
        rr_deleted = translation_manager.get_text("reaction_roles.deleted", interaction.user.id, interaction.guild_id, msg_id=msg_id)
        await interaction.response.send_message(f"🗑️ {rr_deleted}", ephemeral=True)
    else:
        no_rr_attached = translation_manager.get_text("reaction_roles.not_attached", interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(f"❌ {no_rr_attached}", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if bot_locked:
        return
    if payload.user_id == bot.user.id or payload.guild_id is None:
        return
    data = load_reaction_roles(payload.guild_id)
    info = data.get(str(payload.message_id))
    if not info:
        return
    for r in info["reactions"]:
        if str(payload.emoji) == r["emoji"] or getattr(payload.emoji, "name", None) == r["emoji"]:
            guild  = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role   = guild.get_role(r["role_id"])
            if member and role:
                await member.add_roles(role)
                if not r["removable"]:
                    channel = guild.get_channel(info["channel_id"])
                    msg     = await channel.fetch_message(payload.message_id)
                    await msg.remove_reaction(payload.emoji, member)
            break


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if bot_locked:
        return
    if payload.user_id == bot.user.id or payload.guild_id is None:
        return
    data = load_reaction_roles(payload.guild_id)
    info = data.get(str(payload.message_id))
    if not info:
        return
    for r in info["reactions"]:
        if str(payload.emoji) == r["emoji"] or getattr(payload.emoji, "name", None) == r["emoji"]:
            if r["removable"]:
                guild  = bot.get_guild(payload.guild_id)
                member = guild.get_member(payload.user_id)
                role   = guild.get_role(r["role_id"])
                if member and role:
                    await member.remove_roles(role)
            break


# LINK SHORTENER

def format_time(dt_str):
    if not dt_str:
        return translation_manager.get_text("general.never", None, None)
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M")

@bot.tree.command(name="shortenlink", description=get_command_description("shortenlink"))
@app_commands.describe(url=get_parameter_description("url"), custom=get_parameter_description("custom"))
async def shorten(interaction: discord.Interaction, url: str, custom: str = None):
    user_id = str(interaction.user.id)
    username = str(interaction.user.name)
    now = datetime.now(timezone.utc).timestamp()
    cooldown = user_cooldowns.get(user_id, 0)
    if now < cooldown:
        remaining = int(cooldown - now)
        cooldown_text = translation_manager.get_text("url_shortener.cooldown_message", interaction.user.id, interaction.guild_id, minutes=remaining//60, seconds=remaining%60)
        await interaction.response.send_message(cooldown_text, ephemeral=True)
        return
    data = {
        "destination_url": url,
        "discord_id": user_id,
        "discord_username": username
    }
    if custom:
        data["custom"] = custom
    try:
        resp = requests.post(f"{BACKEND_API}/api/shorten", data=data)
        if resp.status_code == 429:
            wait_text = translation_manager.get_text("url_shortener.wait_10min", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(wait_text, ephemeral=True)
            return
        elif resp.status_code == 409:
            exists_text = translation_manager.get_text("url_shortener.link_exists", interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(exists_text, ephemeral=True)
            return
        elif not resp.ok:
            error_text = translation_manager.get_text("url_shortener.error_message", interaction.user.id, interaction.guild_id, error=resp.text)
            await interaction.response.send_message(error_text, ephemeral=True)
            return
        user_cooldowns[user_id] = now + COOLDOWN_SECONDS
        result = resp.json()
        link = result["url"]
        expires = result.get("expires_at")
        link_created_text = translation_manager.get_text("url_shortener.link_created", interaction.user.id, interaction.guild_id, link=link, expires=format_time(expires))
        await interaction.response.send_message(link_created_text, ephemeral=True)
    except Exception as e:
        error_text = translation_manager.get_text("url_shortener.error_message", interaction.user.id, interaction.guild_id, error=str(e))
        await interaction.response.send_message(error_text, ephemeral=True)

@bot.tree.command(name="mylinks", description=get_command_description("mylinks"))
async def mylinks(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = {
        "discord_id": user_id
    }
    try:
        resp = requests.post(f"{BACKEND_API}/api/mylinks", data=data)
        if not resp.ok:
            await interaction.response.send_message(translation_manager.get_text("messages.no_links_error", interaction.user.id, interaction.guild_id), ephemeral=True)
            return
        links = resp.json()
        if not links:
            await interaction.response.send_message(translation_manager.get_text("messages.no_own_links", interaction.user.id, interaction.guild_id), ephemeral=True)
            return
        links_header = translation_manager.get_text("url_shortener.your_links", interaction.user.id, interaction.guild_id)
        msg = f"**{links_header}**\n"
        for l in links:
            expires = format_time(l.get("expires_at"))
            expires_text = translation_manager.get_text("url_shortener.expires", interaction.user.id, interaction.guild_id, expires=expires)
            msg += f"`/{l['short']}` → <{l['destination_url']}>\n{expires_text}\n"
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        error_text = translation_manager.get_text("general.error", interaction.user.id, interaction.guild_id, error=str(e))
        await interaction.response.send_message(error_text, ephemeral=True)

@bot.tree.command(name="extendlink", description=get_command_description("extendlink"))
@app_commands.describe(
    short=get_parameter_description("short"),
    days=get_parameter_description("days"),
    never=get_parameter_description("never")
)
async def extend(interaction: discord.Interaction, short: str, days: int = None, never: bool = False):
    user_id = str(interaction.user.id)
    data = {
        "discord_id": user_id,
        "short": short
    }
    if never:
        data["never"] = "on"
    elif days:
        data["days"] = days
    try:
        resp = requests.post(f"{BACKEND_API}/api/extend", data=data)
        if not resp.ok:
            await interaction.response.send_message(translation_manager.get_text("messages.extend_failed", interaction.user.id, interaction.guild_id), ephemeral=True)
            return
        expires = resp.json().get("expires_at")
        await interaction.response.send_message(translation_manager.get_text("messages.new_expiry_date", interaction.user.id, interaction.guild_id, expires=format_time(expires)), ephemeral=True)
    except Exception as e:
        error_text = translation_manager.get_text("general.error", interaction.user.id, interaction.guild_id, error=str(e))
        await interaction.response.send_message(error_text, ephemeral=True)

@bot.tree.command(name="deletelink", description=get_command_description("deletelink"))
@app_commands.describe(short=get_parameter_description("short"))
async def delete(interaction: discord.Interaction, short: str):
    user_id = str(interaction.user.id)
    data = {
        "discord_id": user_id,
        "short": short
    }
    try:
        resp = requests.post(f"{BACKEND_API}/api/delete", data=data)
        if not resp.ok:
            await interaction.response.send_message(translation_manager.get_text("messages.delete_failed", interaction.user.id, interaction.guild_id), ephemeral=True)
            return
        await interaction.response.send_message(translation_manager.get_text("messages.link_deleted", interaction.user.id, interaction.guild_id), ephemeral=True)
    except Exception as e:
        error_text = translation_manager.get_text("general.error", interaction.user.id, interaction.guild_id, error=str(e))
        await interaction.response.send_message(error_text, ephemeral=True)


#TEMPMAIL

def generate_mailbox_payload(user_id, expires_in_hours=1, domain="wh0ask3d.email", never_expires=False):
    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    user_part = str(user_id)[-4:]
    address = f"discord{random_part}{user_part}@{domain}"

    # Expiration logic
    now = datetime.now(timezone.utc)
    if never_expires:
        expires_at = "2099-12-31T23:59:59"
        cooldown_until = None
    else:
        expires = now + timedelta(hours=expires_in_hours)
        expires_at = expires.isoformat()
        cooldown = now + timedelta(hours=expires_in_hours)
        cooldown_until = cooldown.isoformat()

    payload = {
        "address": address,
        "created_by": str(user_id),
        "expires_at": expires_at,
        "cooldown_until": cooldown_until if not never_expires else None
    }
    return payload

# --- /tempmail command ---
@bot.tree.command(name="tempmail", description=get_command_description("tempmail"))
async def tempmail(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    logging.info(translation_manager.get_text("logging.user_used_tempmail", user_id, None, user=user_id))

    # Get cooldown
    try:
        resp = requests.get(f"{TEMPMAIL_API}/get_cooldown", params={"created_by": user_id}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.error(translation_manager.get_text("logging.cooldown_error", user_id, None, error=str(e)))
        backend_error_text = translation_manager.get_text("tempmail.backend_error", user_id, interaction.guild_id)
        await interaction.response.send_message(backend_error_text, ephemeral=True)
        return

    cooldown_until = data.get("cooldown_until")
    if cooldown_until:
        until_dt = datetime.fromisoformat(cooldown_until)
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
        if until_dt > datetime.now(timezone.utc):
            # Fetch current mailbox
            try:
                mb_resp = requests.get(f"{TEMPMAIL_API}/get_active_mailbox", params={"created_by": user_id}, timeout=5)
                mb = mb_resp.json()
            except Exception as e:
                error_msg = translation_manager.get_text("logging.mailbox_fetch_error", user_id, None, error=str(e))
                logging.error(error_msg)
                mb = {}
            cooldown_text = translation_manager.get_text("tempmail.cooldown_active", user_id, interaction.guild_id, timestamp=int(until_dt.timestamp()))
            msg = cooldown_text
            if mb.get("address"):
                current_email_text = translation_manager.get_text("tempmail.current_email", user_id, interaction.guild_id, email=mb['address'])
                msg += f"\n{current_email_text}"
                if mb.get("expires_at"):
                    exp_dt = datetime.fromisoformat(mb["expires_at"])
                    expires_time_text = translation_manager.get_text("tempmail.expires_time", user_id, interaction.guild_id, timestamp=int(exp_dt.timestamp()))
                    msg += f"\n{expires_time_text}"
                else:
                    expires_never_text = translation_manager.get_text("tempmail.expires_never", user_id, interaction.guild_id)
                    msg += f"\n{expires_never_text}"
            await interaction.response.send_message(msg, ephemeral=True)
            return

    # Create mailbox
    try:
        payload = generate_mailbox_payload(user_id)
        reg_resp = requests.post(f"{TEMPMAIL_API}/register_mailbox", json=payload, timeout=5)
        reg_resp.raise_for_status()
        mb_resp = requests.get(f"{TEMPMAIL_API}/get_active_mailbox", params={"created_by": user_id}, timeout=5)
        mb = mb_resp.json()
        address = mb.get("address")
        expires = mb.get("expires_at")
        if address:
            email_created_text = translation_manager.get_text("tempmail.email_created", user_id, interaction.guild_id, email=address)
            msg = email_created_text
            if expires:
                exp_dt = datetime.fromisoformat(expires)
                valid_until_text = translation_manager.get_text("tempmail.valid_until", user_id, interaction.guild_id, timestamp=int(exp_dt.timestamp()))
                msg += f"\n{valid_until_text}"
            else:
                no_expiry_text = translation_manager.get_text("tempmail.no_expiry", user_id, interaction.guild_id)
                msg += f"\n{no_expiry_text}"
            await interaction.response.send_message(msg, ephemeral=True)
            created_log = translation_manager.get_text("logging.mailbox_created", user_id, None, user=user_id, address=address)
            logging.info(created_log)
            # Register in polling
            user_sessions[user_id] = {
                "address": address,
                "expires": exp_dt.timestamp() if expires else None,
                "last_checked": None
            }
        else:
            creation_failed_text = translation_manager.get_text("tempmail.creation_failed", user_id, interaction.guild_id)
            await interaction.response.send_message(creation_failed_text, ephemeral=True)
            logging.error(translation_manager.get_text("logging.creation_failed_log", user_id, None, response=str(mb)))
    except Exception as e:
        logging.error(translation_manager.get_text("logging.creation_error_log", user_id, None, error=str(e)))
        creation_error_text = translation_manager.get_text("tempmail.creation_error", user_id, interaction.guild_id)
        await interaction.response.send_message(creation_error_text, ephemeral=True)

# --- /resetmail command ---
@bot.tree.command(name="resetmail", description=get_command_description("resetmail"))
async def resetmail(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    used_reset_log = translation_manager.get_text("logging.user_used_resetmail", user_id, None, user=user_id)
    logging.info(used_reset_log)
    try:
        # Delete mailbox(es)
        get_mb = requests.get(f"{TEMPMAIL_API}/get_mailboxes_by_user", params={"created_by": user_id}, timeout=5)
        get_mb.raise_for_status()
        mboxes = get_mb.json()
        logging.info(f"DEBUG: get_mailboxes_by_user returns: {mboxes}")
        for mb in mboxes['mailboxes']:
            resp = requests.post(f"{TEMPMAIL_API}/delete_mailbox", json=mb["address"], timeout=5)
            resp.raise_for_status()
            deleted_log = translation_manager.get_text("logging.mailbox_deleted", user_id, None, address=mb['address'], user=user_id, status=resp.status_code)
            logging.info(deleted_log)
        # Reset cooldown
        resp2 = requests.post(f"{TEMPMAIL_API}/reset_cooldown", json=user_id, timeout=5)
        resp2.raise_for_status()
        cooldown_reset_log = translation_manager.get_text("logging.cooldown_reset", user_id, None, user=user_id)
        logging.info(cooldown_reset_log)
        reset_success_text = translation_manager.get_text("tempmail.reset_success", user_id, interaction.guild_id)
        await interaction.response.send_message(f"🗑️ {reset_success_text}", ephemeral=True)
        # Remove from polling
        if user_id in user_sessions:
            del user_sessions[user_id]
    except Exception as e:
        reset_error_log = translation_manager.get_text("logging.reset_error", user_id, None, user=user_id, error=str(e))
        logging.error(reset_error_log)
        reset_error_text = translation_manager.get_text("tempmail.reset_error", user_id, interaction.guild_id)
        await interaction.response.send_message(reset_error_text, ephemeral=True)

# EVENT LISTENERY

@bot.event
async def on_raw_message_delete(payload: RawMessageDeleteEvent):
    if bot_locked:
        return
    if payload.guild_id is None:
        return
    guild_id = payload.guild_id
    guild = bot.get_guild(guild_id)
    await asyncio.sleep(1)
    info = message_store.get(guild_id, {}).pop(str(payload.message_id), None)
    log_dir = ensure_guild_log_dir(guild_id)
    path_main = os.path.join(log_dir, f'{guild_id}_main.json')
    main_entries = []
    if os.path.exists(path_main):
        with open(path_main, 'r', encoding='utf-8') as f:
            main_entries = json.load(f)
    main_entries = [e for e in main_entries if e["message_id"] != payload.message_id]
    with open(path_main, 'w', encoding='utf-8') as f:
        json.dump(main_entries, f, ensure_ascii=False, indent=2)
    author = guild.get_member(info["author_id"]) or await bot.fetch_user(info["author_id"]) if info else translation_manager.get_text("logging.author_not_found", None, guild_id)
    content = info["content"] if info else translation_manager.get_text("logging.no_content", None, guild_id)
    now = datetime.now(timezone.utc)
    executor = None
    async for e in guild.audit_logs(limit=5, action=AuditLogAction.message_delete):
        if e.extra.channel.id != payload.channel_id:
            continue
        delta = (now - e.created_at).total_seconds()
        if delta > 5:
            break
        if e.extra.count != 1:
            continue
        if hasattr(author, 'id') and e.user.id == author.id:
            executor = None
        else:
            executor = e.user
        break
    await log_action(
        guild, "MessageDelete",
        user=author, executor=executor,
        fields=[
            {"name": "Channel ID",   "value": str(payload.channel_id),    "inline": False},
            {"name": "Message ID",   "value": str(payload.message_id),    "inline": False},
            {"name": translation_manager.get_text("logging.content", None, guild_id),        "value": content,                   "inline": False}
        ]
    )
    path_deleted = os.path.join(log_dir, f'{guild_id}_deleted.json')
    deleted_logs = []
    if os.path.exists(path_deleted):
        with open(path_deleted, 'r', encoding='utf-8') as f:
            deleted_logs = json.load(f)
    deleted_entry = {
        "message_id": payload.message_id,
        "channel_id": payload.channel_id,
        "author_id": info["author_id"] if info else None,
        "content": content,
        "deleted_by": executor.id if executor else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    deleted_logs.append(deleted_entry)
    with open(path_deleted, 'w', encoding='utf-8') as f:
        json.dump(deleted_logs, f, ensure_ascii=False, indent=2)

@bot.event
async def on_raw_message_edit(payload: RawMessageUpdateEvent):
    if bot_locked:
        return
    if payload.guild_id is None:
        return
    guild_id = payload.guild_id
    guild = bot.get_guild(guild_id)
    log_dir = ensure_guild_log_dir(guild_id)
    path_main = os.path.join(log_dir, f'{guild_id}_main.json')
    main_entries = []
    if os.path.exists(path_main):
        with open(path_main, 'r', encoding='utf-8') as f:
            main_entries = json.load(f)
    old = next((e["content"] for e in main_entries if e["message_id"] == payload.message_id), translation_manager.get_text("logging.no_content", None, guild_id))
    channel = guild.get_channel(payload.channel_id)
    try:
        new_msg = await channel.fetch_message(payload.message_id)
        new = new_msg.content
    except:
        new = translation_manager.get_text("logging.no_content", None, guild_id)
    for e in main_entries:
        if e["message_id"] == payload.message_id:
            e["content"] = new
            break
    with open(path_main, 'w', encoding='utf-8') as f:
        json.dump(main_entries, f, ensure_ascii=False, indent=2)
    message_store.setdefault(guild_id, {})[str(payload.message_id)] = {"author_id": message_store.get(guild_id, {}).get(str(payload.message_id), {}).get("author_id"), "content": new}
    fields = [
        {"name": "Channel ID", "value": str(payload.channel_id), "inline": False},
        {"name": "Message ID", "value": str(payload.message_id), "inline": False},
        {"name": translation_manager.get_text("logging.before", None, guild_id),      "value": old,                     "inline": False},
        {"name": translation_manager.get_text("logging.after", None, guild_id),         "value": new,                     "inline": False}
    ]
    await log_action(guild, "MessageEdit", user=new_msg.author if 'new_msg' in locals() else translation_manager.get_text("logging.unknown_author", None, guild_id), fields=fields)
    path_mod = os.path.join(log_dir, f'{guild_id}_modified.json')
    mod_logs = []
    if os.path.exists(path_mod):
        with open(path_mod, 'r', encoding='utf-8') as f:
            mod_logs = json.load(f)
    mod_entry = {
        "message_id": payload.message_id,
        "channel_id": payload.channel_id,
        "author_id": message_store.get(guild_id, {})
                    .get(str(payload.message_id), {})
                    .get("author_id"),
        "before": old,
        "after": new,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    mod_logs.append(mod_entry)
    with open(path_mod, 'w', encoding='utf-8') as f:
        json.dump(mod_logs, f, ensure_ascii=False, indent=2)




@bot.event
async def on_guild_channel_create(channel):
    if bot_locked:
        return
    actor = None
    async for e in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
        if getattr(e.target, "id", None) == channel.id:
            actor = e.user
            break
    await log_action(channel.guild, "ChannelCreate", actor or channel.guild.me, reason=channel.mention)

@bot.event
async def on_guild_channel_delete(channel):
    if bot_locked:
        return
    actor = None
    async for e in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        if getattr(e.target, "id", None) == channel.id:
            actor = e.user
            break
    await log_action(channel.guild, "ChannelDelete", actor or channel.guild.me, reason=f"`#{channel.name}`")

@bot.event
async def on_guild_channel_update(before, after):
    if bot_locked:
        return
    actor = None
    async for e in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_update):
        if getattr(e.target, "id", None) == after.id:
            actor = e.user
            break
    reason = f"`{before.name}` ➞ `{after.name}`"
    await log_action(after.guild, "ChannelUpdate", actor or after.guild.me, reason=reason)

@bot.event
async def on_guild_role_create(role):
    if bot_locked:
        return
    actor = None
    async for e in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_create):
        if getattr(e.target, "id", None) == role.id:
            actor = e.user
            break
    await log_action(role.guild, "RoleCreate", actor or role.guild.me, reason=str(role.id))

@bot.event
async def on_guild_role_delete(role):
    if bot_locked:
        return
    try:
        async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
            actor = None
            if getattr(entry.target, "id", None) == role.id:
                actor = entry.user
                break
            await log_action(role.guild, "RoleDelete", actor or role.guild.me, reason=f"{role.name}")
            break
    except discord.errors.NotFound:
        guild_id_str = getattr(role.guild, 'id', translation_manager.get_text("logging.missing", None, None))
        error_msg = translation_manager.get_text("logging.role_delete_logs_error", None, None, guild_id=guild_id_str)
        print(f"[role_delete] {error_msg}")
        return
    except Exception as e:
        other_error_msg = translation_manager.get_text("logging.other_error", None, None, error=str(e))
        print(f"[role_delete] {other_error_msg}")

@bot.event
async def on_guild_role_delete(role):
    if bot_locked:
        return
    actor = None
    async for e in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        if getattr(e.target, "id", None) == role.id:
            actor = e.user
            break
    await log_action(role.guild, "RoleDelete", actor or role.guild.me, reason=f"{role.name}")

@bot.event
async def on_member_update(before, after):
    if bot_locked:
        return
    if not before.guild:
        return
    added = [r for r in after.roles if r not in before.roles]
    for role in added:
        await log_action(before.guild, "RoleAdd", after, reason=str(role.mention))
    removed = [r for r in before.roles if r not in after.roles]
    for role in removed:
        await log_action(before.guild, "RoleRemove", after, reason=str(role.mention))

@bot.event
async def on_member_join(member):
    if bot_locked:
        return
    ch_id = load_channel_data("welcomers").get(str(member.guild.id))
    if ch_id:
        ch = member.guild.get_channel(int(ch_id))
        if ch:
            new_member_title = translation_manager.get_text("welcome.new_member", None, member.guild.id)
            greeting = translation_manager.get_text("welcome.greeting", None, member.guild.id, member=member.mention, guild=member.guild.name)
            member_count_label = translation_manager.get_text("welcome.member_count", None, member.guild.id)
            embed = discord.Embed(
                title=new_member_title,
                description=greeting,
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name=member_count_label, value=str(member.guild.member_count), inline=False)
            embed.set_footer(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            await ch.send(embed=embed)
    ping_id = load_channel_data("pingers").get(str(member.guild.id))
    if ping_id:
        ping = member.guild.get_channel(int(ping_id))
        if ping:
            ping_msg = await ping.send(member.mention)
            await asyncio.sleep(1)
            await ping_msg.delete()

        
    before = guild_invites.get(member.guild.id, [])
    after = await member.guild.invites()
    guild_invites[member.guild.id] = after

    inviter = None
    for inv_new in after:
        for inv_old in before:
            if inv_new.code == inv_old.code and inv_new.uses > inv_old.uses:
                inviter = inv_new.inviter
                break
        if inviter:
            break
    if inviter:
        reason = translation_manager.get_text("logging.invited_by", None, member.guild.id, inviter=inviter.mention)
    else:
        reason = translation_manager.get_text("logging.invited", None, member.guild.id)
    await log_action(member.guild, "MemberJoin", member, reason=reason)

@bot.event
async def on_member_remove(member):
    if bot_locked:
        return
    join = member.joined_at
    if join:
        reason = translation_manager.get_text("logging.member_since", None, member.guild.id, date=join.strftime('%Y-%m-%d %H:%M:%S'))
    else:
        reason = translation_manager.get_text("logging.no_join_date", None, member.guild.id)
    await log_action(member.guild, "MemberLeave", member, reason=reason)


bot.run(token)
# ==================== BOT START ====================
if __name__ == '__main__':
    if not token:
        print('❌ DISCORD_TOKEN not found in environment variables!')
        sys.exit(1)
    print('🚀 Starting bot...')
    bot.run(token)
