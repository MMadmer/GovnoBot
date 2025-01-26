import matplotlib.pyplot as plt
from datetime import datetime
import io
import discord
from loguru import logger
import aiohttp


async def crypto_chart(interaction: discord.Interaction, currency: str = "bitcoin", days: int = 7):
    """
    Builds a chart of the cryptocurrency price over the last specified number of days.
    """
    await interaction.response.defer()

    url = f"https://api.coingecko.com/api/v3/coins/{currency}/market_chart"
    params = {"vs_currency": "usd", "days": str(days)}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"Error fetching data for {currency}. Ensure the currency name is correct.", ephemeral=True)
                    return

                data = await resp.json()

        # Extract prices and timestamps
        prices = [point[1] for point in data["prices"]]
        timestamps = [datetime.fromtimestamp(point[0] / 1000) for point in data["prices"]]

        # Plot the data
        plt.figure(figsize=(10, 5))
        plt.plot(timestamps, prices, label=f"{currency.capitalize()} (USD)")
        plt.title(f"{currency.capitalize()} Price (Last {days} Days)")
        plt.xlabel("Date")
        plt.ylabel("Price (USD)")
        plt.grid()
        plt.legend()

        # Save the plot to a buffer
        buffer = io.BytesIO()
        plt.savefig(buffer, format="png")
        buffer.seek(0)
        plt.close()

        # Send the plot to Discord
        await interaction.followup.send(file=discord.File(buffer, f"{currency}_chart.png"))

    except Exception as e:
        logger.error(f"Error generating chart: {e}")
        await interaction.followup.send("An error occurred while generating the chart. Please try again.",
                                        ephemeral=True)
