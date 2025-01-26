import asyncio
import discord
from discord.ext import commands
import os
from loguru import logger
from discord.ext import tasks
import aiohttp
import json
import functools
import re
from datetime import datetime
import pytz

import coinplot

logger.add("bot.log", rotation="10 MB", level="INFO")


def calculate_percentage(part: int, total: int) -> float:
    return (part / total * 100) if total > 0 else 0


def cancel_on_rate_limit(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Exception 429 processing
                logger.warning("Rate limited. Skipping this update.")
                return  # Stop update
            else:
                logger.error(f"HTTPException: {e}")
                raise  # Raise other exceptions
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise

    return wrapper


def is_admin(ctx: commands.Context):
    return ctx.guild is not None and ctx.author.guild_permissions.administrator


def is_dm(ctx: commands.Context):
    return ctx.guild is None


class GovnoBot(commands.Bot):
    def __init__(self, command_prefix: str, token: str):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.token = token
        self.add_commands()
        self.data_folder = "/data/" if os.name != 'nt' else "data/"

        self.name = "GOVNO"
        self.token_address = "EQAf2LUJZMdxSAGhlp-A60AN9bqZeVM994vCOXH05JFo-7dc"
        self.chain = "ton"
        self.currency = "usd"

        self.refresh_rate = 600  # Discord don't like timers less than 10 minutes
        self.price_channel = None

        self.info_channel = None
        self.info_message_id = None

    async def on_ready(self):
        logger.info(f'Logged in as {self.user}')

        self.load_data()

        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logger.warning(e)

        self.refresh_price.change_interval(seconds=self.refresh_rate)
        self.refresh_price.start()

        self.refresh_info.change_interval(seconds=self.refresh_rate)
        self.refresh_info.start()

    def run_bot(self):
        if not os.path.exists(self.data_folder):
            logger.info(f"Creating data folder at {self.data_folder}")
            os.makedirs(self.data_folder)

        logger.info("Starting bot...")
        self.run(self.token)

    def add_commands(self):
        @self.tree.command(name="course", description="Returns current price of $GOVNO in $USD.")
        async def send_course(interaction: discord.Interaction):
            await interaction.response.defer()
            js = await self.get_token_info()
            if not js:
                await interaction.followup.send("Price unavailable")
                return

            attributes = js.get("data", {}).get("attributes", {})
            price = attributes.get("price_usd")
            name = attributes.get("name", "Unknown Token")

            await interaction.followup.send(f"💩 {name} ${price}" if price else "")

        @self.tree.command(name="force_update_price",
                           description="Force update price for price channel. Channel must be set.")
        @commands.has_permissions(administrator=True)
        async def force_refresh_price(interaction: discord.Interaction):
            if not self.price_channel:
                await interaction.response.send_message("Price channel is not set.", ephemeral=True)
                return

            self.refresh_price.restart()
            await interaction.response.send_message("Price channel name has been updated.")

        @self.tree.command(name="assign_price_channel", description="Assign a channel to display current token price.")
        @commands.has_permissions(administrator=True)
        async def assign_channel_on_price(interaction: discord.Interaction, channel: discord.TextChannel):
            self.price_channel = channel
            self.refresh_price.restart()
            self.save_data()  # Saving
            logger.info(f"Price channel set to: {channel.name}")
            await interaction.response.send_message(f"Price updates will be displayed in: {channel.mention}")

        @self.tree.command(name="assign_info_channel", description="Assign a channel to display a token info.")
        @commands.has_permissions(administrator=True)
        async def assign_info_channel(interaction: discord.Interaction, channel: discord.TextChannel,
                                      message_link: str = None):
            self.info_channel = channel

            if message_link:
                try:
                    message_id = int(message_link.split("/")[-1])
                    message = await channel.fetch_message(message_id)
                    self.info_message_id = message.id
                    logger.info(f"Using existing message with ID: {self.info_message_id}")
                except Exception as e:
                    logger.error(f"Failed to fetch the message from the link: {e}")
                    await interaction.response.send_message(
                        "Failed to use the provided message link. Check the link and try again.", ephemeral=True)
                    return
            else:
                message = await channel.send("Token info")
                self.info_message_id = message.id
                logger.info(f"Info channel set to: {channel.name}, new message created.")

            self.save_data()  # Saving
            await interaction.response.send_message(f"Token info updates will be displayed in: {channel.mention}",
                                                    ephemeral=True)
            self.refresh_info.restart()

        @self.tree.command(name="price_plot", description="Displays a price chart of the selected cryptocurrency.")
        async def make_price_plot(interaction: discord.Interaction, currency: str = "bitcoin", days: int = 7):
            await coinplot.crypto_chart(interaction=interaction, currency=currency, days=days)

    async def on_command_error(self, interaction: discord.Interaction, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await interaction.response.send_message("You do not have the necessary permissions to run this command.",
                                                    ephemeral=True)
        else:
            logger.error(f"An error occurred: {error}")
            await interaction.response.send_message("An unexpected error occurred.", ephemeral=True)

    async def get_token_info(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.geckoterminal.com/api/v2/networks/{self.chain}/pools/{self.token_address}",
                        timeout=10
                ) as r:
                    if r.status == 200:
                        return await r.json()
                    else:
                        logger.error(f"API responded with status code {r.status}")
                        return {}
        except aiohttp.ClientError as e:
            logger.error(f"Network error while fetching token info: {e}")
            return {}
        except asyncio.TimeoutError:
            logger.error("Request to API timed out")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {}

    @tasks.loop(seconds=600)
    @cancel_on_rate_limit
    async def refresh_info(self):
        if not self.info_channel or not self.info_message_id:
            logger.warning("Message info channel or info message ID is not set. Skipping update.")
            return

        try:
            # Get info message
            channel = self.info_channel
            message = await channel.fetch_message(self.info_message_id)

            # Get JSON from API
            js = await self.get_token_info()
            if not js:
                logger.error("Price data unavailable")
                return

            # Parse JSON
            attributes = js.get("data", {}).get("attributes", {})
            price = attributes.get("base_token_price_usd", "N/A")
            price_native = attributes.get("base_token_price_native_currency", "N/A")
            name = attributes.get("name", "Unknown Token")
            fdv = attributes.get("fdv_usd", "N/A")
            reserve_usd = attributes.get("reserve_in_usd", "N/A")
            volume_usd_24h = attributes.get("volume_usd", {}).get("h24", "N/A")
            price_change_24h = attributes.get("price_change_percentage", {}).get("h24", "N/A")

            transactions_24h = attributes.get("transactions", {}).get("h24", {})
            buys_24h = transactions_24h.get("buys", 0)
            sells_24h = transactions_24h.get("sells", 0)
            buyers_24h = transactions_24h.get("buyers", 0)
            sellers_24h = transactions_24h.get("sellers", 0)

            # Data formatting
            price = f"{float(price):,.2f}" if price not in ["N/A", None] else "N/A"
            price_native = f"{float(price_native):,.2f}" if price_native not in ["N/A", None] else "N/A"
            fdv = f"{int(fdv):,}" if fdv not in ["N/A", None] else "N/A"
            reserve_usd = f"{float(reserve_usd):,.2f}" if reserve_usd not in ["N/A", None] else "N/A"
            volume_usd_24h = f"{float(volume_usd_24h):,.2f}" if volume_usd_24h not in ["N/A", None] else "N/A"

            name = attributes.get("name", "Unknown Token")
            match = re.match(r"^(.*?)(?:\s\S+%?)?$", name)
            name = match.group(1) if match else "Unknown Token"

            try:
                price_change_24h = float(price_change_24h) if price_change_24h not in ["N/A", None] else None
                if price_change_24h is not None:
                    price_change_24h = f"{'+' if price_change_24h > 0 else ''}{price_change_24h:,.2f}%"
                else:
                    price_change_24h = "N/A"
            except ValueError:
                price_change_24h = "N/A"

            # Make Embed
            embed = discord.Embed(
                title=f"💩 {name}",
                description=(
                    f"**Price (USD):** ${price}\n"
                    f"**Price (TON):** {price_native}\n\n"
                    f"**FDV:** ${fdv}\n"
                    f"**Reserve (USD):** ${reserve_usd}"
                ),
                color=discord.Color.gold()
            )

            # Additional
            embed.add_field(name="Volume (24h)", value=f"${volume_usd_24h}", inline=True)
            embed.add_field(name="Price Change (24h)", value=f"{price_change_24h}%", inline=True)

            # Transactions
            total_transactions = buys_24h + sells_24h
            total_participants = buyers_24h + sellers_24h

            embed.add_field(name="", value="", inline=False)

            embed.add_field(
                name="Buys / Sells (24h)",
                value=(
                    f"**Buys:** {buys_24h} ({calculate_percentage(buys_24h, total_transactions):.2f}%)\n"
                    f"**Sells:** {sells_24h} ({calculate_percentage(sells_24h, total_transactions):.2f}%)"
                ),
                inline=True
            )
            embed.add_field(
                name="Buyers / Sellers (24h)",
                value=(
                    f"**Buyers:** {buyers_24h} ({calculate_percentage(buyers_24h, total_participants):.2f}%)\n"
                    f"**Sellers:** {sellers_24h} ({calculate_percentage(sellers_24h, total_participants):.2f}%)"
                ),
                inline=True
            )

            # Footer time
            moscow_tz = pytz.timezone("Europe/Moscow")
            now = datetime.now(moscow_tz)

            last_updated = now.strftime("%Y-%m-%d %H:%M:%S")
            gmt_offset = now.utcoffset()
            gmt_formatted = f"GMT{gmt_offset.total_seconds() // 3600:+03.0f}:00"
            last_updated_with_gmt = f"{last_updated} {gmt_formatted}"

            embed.set_footer(text=f"Updated every 10 minutes | Last update: {last_updated_with_gmt}")

            # Load token image
            image_filename = "govno.jpg"
            file_path = f"./images/{image_filename}"
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    icon_file = discord.File(f, filename=image_filename)
                    embed.set_thumbnail(url=f"attachment://{image_filename}")
                    await message.edit(embed=embed, attachments=[icon_file])
            else:
                logger.warning(f"Image file not found: {file_path}")
                await message.edit(embed=embed)
        except Exception as e:
            logger.error(f"Error while updating message: {e}")

    @tasks.loop(seconds=600)
    @cancel_on_rate_limit
    async def refresh_price(self):
        if not self.price_channel:
            logger.warning("Price channel is not set. Skipping update.")
            return

        try:
            guild = discord.utils.get(self.guilds, id=self.price_channel.guild.id)
            if not guild:
                logger.error("Guild not found for price channel.")
                return

            channel = guild.get_channel(self.price_channel.id)
            if not channel:
                logger.error("Price channel not found.")
                return

            # Get JSON from API
            js = await self.get_token_info()

            if not js:
                logger.error("Price data unavailable")
                return

            attributes = js.get("data", {}).get("attributes", {})
            price = attributes.get("base_token_price_usd")
            price = f"${float(price):,.2f}" if price not in ["N/A", None] else "N/A"

            # Get current price
            price_info = f"╭💩・💲{str(price).replace('.', '․')}" if price else ""

            # Update channel name
            await channel.edit(name=price_info)
            logger.info(f"Updated channel name to: {price_info}")
        except Exception as e:
            logger.error(f"Error while updating channel name: {e}")

    def save_data(self):
        if not os.path.exists(self.data_folder):
            os.makedirs(self.data_folder)
        data = {
            "price_channel": self.price_channel.id if self.price_channel else None,
            "info_channel": self.info_channel.id if self.info_channel else None,
            "info_message_id": self.info_message_id
        }
        with open(os.path.join(self.data_folder, "channels.json"), "w") as f:
            json.dump(data, f)
        logger.info("Info channel and message data saved.")

    def load_data(self):
        try:
            with open(os.path.join(self.data_folder, "channels.json"), "r") as f:
                data = json.load(f)
                if data.get("price_channel"):
                    self.price_channel = self.get_channel(data["price_channel"])
                    logger.info(f"Loaded price channel: {self.price_channel}")
                if data.get("info_channel"):
                    self.info_channel = self.get_channel(data["info_channel"])
                    logger.info(f"Loaded info channel: {self.info_channel}")
                self.info_message_id = data.get("info_message_id")
                if self.info_message_id:
                    logger.info(f"Loaded info message ID: {self.info_message_id}")
        except FileNotFoundError:
            logger.warning("No saved channel data found. Starting fresh.")
        except Exception as e:
            logger.error(f"Error loading channel data: {e}")


if __name__ == '__main__':
    TOKEN = os.environ["BOT_TOKEN"]
    COMMAND_PREFIX = '!'

    bot = GovnoBot(command_prefix=COMMAND_PREFIX, token=TOKEN)
    bot.run_bot()
