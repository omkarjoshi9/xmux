import discord


def create_client(gateway, env):
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True
    intents.message_content = env.get("DISCORD_MESSAGE_CONTENT", "").lower() == "true"

    class Client(discord.Client):
        async def on_message(self, message):
            adapter = gateway.adapters["discord"]
            for event in adapter.parse_message(message):
                gateway.intake(event)

    return Client(intents=intents)
