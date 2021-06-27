from inspect import cleandoc
from os import getpid
from time import monotonic

import discord
from discord.ext import commands
from psutil import Process

import utils


start_time = monotonic()

def setup(bot):
    bot.add_cog(cmds_extra(bot))

class cmds_extra(utils.CommonCog, name="Extra Commands"):
    "TTS Bot extra commands, not required but useful."

    @commands.command()
    async def uptime(self, ctx: commands.Context):
        "Shows how long TTS Bot has been online"
        await ctx.send(f"{self.bot.user.mention} has been up for {(monotonic() - start_time) / 60:.2f} minutes")

    @commands.bot_has_permissions(send_messages=True)
    @commands.command(hidden=True)
    async def tts(self, ctx: commands.Context):
        if ctx.message.content == f"{ctx.prefix}tts":
            await ctx.send(f"You don't need to do `{ctx.prefix}tts`! {self.bot.user.mention} is made to TTS any message, and ignore messages starting with `{ctx.prefix}`!")

    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    @commands.command(aliases=["info", "stats"])
    async def botstats(self, ctx: commands.Context):
        "Shows various different stats"

        channels = len(self.bot.voice_clients)

        main_section = cleandoc(f"""
          Currently in:
            :small_blue_diamond: {channels} voice channels
            :small_blue_diamond: {len(self.bot.guilds)} servers
          Currently using:
            :small_orange_diamond: {len(self.bot.shards)} shards
            :small_orange_diamond: {Process(getpid()).memory_info().rss / 1024 ** 2:.1f}MB of RAM
          and can be used by {sum(guild.member_count for guild in self.bot.guilds if not guild.unavailable):,} people!
        """)

        footer = cleandoc("""
            Support Server: https://discord.gg/zWPWwQC
            Repository: https://github.com/Gnome-py/Discord-TTS-Bot
        """)

        embed = discord.Embed(title=f"{self.bot.user.name}: Now open source!", description=main_section, url="https://discord.gg/zWPWwQC", color=0x3498db)
        embed.set_thumbnail(url=str(self.bot.user.avatar_url))
        embed.set_footer(text=footer)

        await ctx.send(embed=embed)

    @commands.bot_has_permissions(send_messages=True)
    @commands.guild_only()
    @commands.command()
    async def channel(self, ctx: utils.TypedGuildContext):
        "Shows the current setup channel!"
        channel = await self.bot.settings.get(ctx.guild, "channel")

        if channel == ctx.channel.id:
            await ctx.send("You are in the right channel already!")
        elif channel != 0:
            await ctx.send(f"The current setup channel is: <#{channel}>")
        else:
            await ctx.send(f"The channel hasn't been setup, do `{ctx.prefix}setup #textchannel`")

    @commands.command()
    @commands.bot_has_permissions(send_messages=True)
    async def donate(self, ctx: commands.Context):
        "Shows how you can help support TTS Bot's development and hosting!"

        await ctx.send(cleandoc(f"""
            To donate to support the development and hosting of {self.bot.user.mention}, you can donate via Patreon (Fees) or directly via DonateBot.io!
            <https://donatebot.io/checkout/693901918342217758>
            https://www.patreon.com/Gnome_the_Bot_Maker
        """))

    @commands.command(aliases=["lag"], hidden=True)
    @commands.bot_has_permissions(send_messages=True)
    async def ping(self, ctx: commands.Context):
        "Gets current ping to discord!"

        ping_before = monotonic()
        ping_message = await ctx.send("Loading!")
        ping = (monotonic() - ping_before) * 1000
        await ping_message.edit(content=f"Current Latency: `{ping:.0f}ms`")

    @commands.command()
    @commands.bot_has_permissions(send_messages=True)
    async def suggest(self, ctx: commands.Context, *, suggestion: str):
        "Suggests a new feature!"

        if suggestion.lower().replace("*", "") == "suggestion":
            return await ctx.send("Hey! You are meant to replace `*suggestion*` with your actual suggestion!")

        if not await self.bot.userinfo.get("blocked", ctx.message.author, default=False):
            files = [await attachment.to_file() for attachment in ctx.message.attachments]
            await self.bot.channels["suggestions"].send(
                suggestion,
                files=files,
                username=str(ctx.author),
                avatar_url=ctx.author.avatar_url
            )

        await ctx.send("Suggestion noted")

    @commands.command()
    @commands.bot_has_permissions(send_messages=True)
    async def invite(self, ctx: commands.Context):
        "Sends the instructions to invite TTS Bot and join the support server!"
        if ctx.guild == self.bot.support_server:
            await ctx.send(f"Check out <#694127922801410119> to invite {self.bot.user.mention}!")
        else:
            invite_channel = self.bot.invite_channel
            invite_channel = invite_channel.name if invite_channel else "deleted-channel"
            await ctx.send(f"Join https://discord.gg/zWPWwQC and look in #{invite_channel} to invite {self.bot.user.mention}!")
