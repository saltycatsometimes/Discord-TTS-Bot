from __future__ import annotations

import asyncio
from inspect import cleandoc
from io import BytesIO
from shlex import split
from subprocess import PIPE, Popen, SubprocessError
from typing import TYPE_CHECKING, Any, Optional, Sequence, Tuple, Union, cast

import asyncgTTS
import discord
import voxpopuli
from discord.ext import tasks
from discord.opus import Encoder
from mutagen import mp3 as mutagen
from pydub import AudioSegment

import utils


if TYPE_CHECKING:
    from main import TTSBot



class FFmpegPCMAudio(discord.AudioSource):
    """TEMP FIX FOR DISCORD.PY BUG
    Orignal Source = https://github.com/Rapptz/discord.py/issues/5192
    Currently fixes `io.UnsupportedOperation: fileno` when piping a file-like object into FFmpegPCMAudio
    If this bug is fixed, notify me via Discord (Gnome!#6669) or PR to remove this file with a link to the discord.py commit that fixes this.
    """
    def __init__(self, source, *, executable="ffmpeg", pipe=False, stderr=None, before_options=None, options=None):
        stdin = source if pipe else None
        args = [executable]

        if isinstance(before_options, str):
            args.extend(split(before_options))

        args.append("-i")
        args.append("-" if pipe else source)
        args.extend(("-f", "s16le", "-ar", "48000", "-ac", "2", "-loglevel", "warning"))

        if isinstance(options, str):
            args.extend(split(options))

        args.append("pipe:1")
        self._process = None
        try:
            self._process = Popen(args, stdin=PIPE, stdout=PIPE, stderr=stderr)
            self._stdout = BytesIO(self._process.communicate(input=stdin)[0])
        except FileNotFoundError:
            raise discord.ClientException(f"{executable} was not found.") from None
        except SubprocessError as exc:
            raise discord.ClientException(f"Popen failed: {exc.__class__.__name__}: {exc}") from exc

    def read(self):
        ret = self._stdout.read(Encoder.FRAME_SIZE)
        if len(ret) != Encoder.FRAME_SIZE:
            return b""
        return ret

    def cleanup(self):
        proc = self._process
        if proc is None:
            return
        proc.kill()
        if proc.poll() is None:
            proc.communicate()

        self._process = None


_AudioData = Tuple[bytes, Union[int, float]]
_MessageQueue = Tuple[discord.Message, str, str]
class TTSVoicePlayer(discord.VoiceClient):
    bot: TTSBot
    guild: discord.Guild
    channel: Union[discord.VoiceChannel, discord.StageChannel]

    def __init__(self, bot: TTSBot, channel: discord.VoiceChannel):
        super().__init__(bot, channel)

        self.bot = bot
        self.prefix = None
        self.linked_channel = 0

        self.currently_playing = asyncio.Event()
        self.currently_playing.set()

        self.audio_buffer: asyncio.Queue[_AudioData] = asyncio.Queue(maxsize=5)
        self.message_queue: asyncio.Queue[_MessageQueue] = asyncio.Queue()

        self.fill_audio_buffer.start()

    def __repr__(self):
        c = self.channel.id
        abufferlen = self.audio_buffer.qsize()
        mqueuelen = self.message_queue.qsize()
        playing_audio = not self.currently_playing.is_set()

        return f"<TTSVoicePlayer: {c=} {playing_audio=} {mqueuelen=} {abufferlen=}>"


    async def disconnect(self, *, force: bool = False) -> None:
        await super().disconnect(force=force)
        self.fill_audio_buffer.cancel()
        self.play_audio.cancel()


    async def queue(self, message: discord.Message, text: str, lang: str, linked_channel: int, prefix: str, max_length: int = 30) -> None:
        self.prefix = prefix
        self.max_length = max_length
        self.linked_channel = linked_channel

        await self.message_queue.put((message, text, lang))
        if not self.fill_audio_buffer.is_running:
            self.fill_audio_buffer.start()

    def skip(self):
        self.message_queue = asyncio.Queue()
        self.audio_buffer = asyncio.Queue(maxsize=5)

        self.stop()
        self.play_audio.restart()
        self.fill_audio_buffer.restart()


    @tasks.loop()
    @utils.decos.handle_errors
    async def play_audio(self):
        self.currently_playing.clear()
        audio, length = await self.audio_buffer.get()

        try:
            self.play(
                FFmpegPCMAudio(audio, pipe=True, options='-loglevel "quiet"'),
                after=self._after_player # type: ignore
            )
        except discord.ClientException:
            self.currently_playing.set()

        try:
            await asyncio.wait_for(self.currently_playing.wait(), timeout=length+5)
        except asyncio.TimeoutError:
            await self.bot.channels["errors"].send(cleandoc(f"""
                ```asyncio.TimeoutError```
                `{self.guild.id}`'s vc.play didn't finish audio!
            """))

    @tasks.loop()
    @utils.decos.handle_errors
    async def fill_audio_buffer(self):
        message, text, lang = await self.message_queue.get()

        lang = lang.split("-")[0]
        get_tts = self.get_espeak if self.bot.blocked else self.get_gtts
        ret_values = await asyncio.wait_for(get_tts(message, text, lang), timeout=10)

        if not ret_values or len(ret_values) == 1:
            return

        audio, file_length = ret_values
        if not audio or file_length > self.max_length:
            return

        await self.audio_buffer.put((audio, file_length))
        if not self.play_audio.is_running():
            self.play_audio.start()


    async def get_gtts(self, message: discord.Message, text: str, lang: str) -> Optional[_AudioData]:
        cached_mp3 = await self.bot.cache.get(text, lang)
        if cached_mp3:
            return cached_mp3, mutagen.MP3(BytesIO(cached_mp3)).info.length

        try:
            audio = await self.bot.gtts.get(text=text, lang=lang)
        except asyncgTTS.RatelimitException:
            if self.bot.blocked:
                return

            self.bot.blocked = True
            if await self.bot.check_gtts() is not True:
                asyncio.create_task(self._handle_rl())
            else:
                self.bot.blocked = False

            return await self.get_gtts(message, text, lang)

        except asyncgTTS.easygttsException as e:
            if str(e)[:3] not in {"400", "500"}:
                raise

            return

        file_length = mutagen.MP3(BytesIO(audio)).info.length
        if audio and file_length <= self.max_length:
            await self.bot.cache.set(text, lang, audio)

        return audio, file_length

    async def get_espeak(self, _: Any, text: str, lang: str) -> _AudioData:
        if text.startswith("-") and " " not in text:
            text += " " # fix espeak hang

        wav = await voxpopuli.Voice(
            lang=utils.GTTS_ESPEAK_DICT.get(lang, "en"),
            speed=130, volume=2
        ).to_audio(text)

        pydub_wav = AudioSegment.from_file_using_temporary_files(BytesIO(wav))
        audio_length = len(pydub_wav)/1000 # type: ignore

        return wav, audio_length


    # easygTTS -> espeak handling
    async def _handle_rl(self):
        await self.bot.channels["logs"].send("**Swapping to espeak**")

        asyncio.create_task(self._handle_rl_reset())
        if not self.bot.sent_fallback:
            self.bot.sent_fallback = True

            await asyncio.gather(*(
                vc._send_fallback() for vc in self.bot.voice_clients
            ))
            await self.bot.channels["logs"].send("**Fallback/RL messages have been sent.**")

    async def _handle_rl_reset(self):
        await asyncio.sleep(3601)
        while True:
            ret = await self.bot.check_gtts()
            if ret:
                break
            elif isinstance(ret, Exception):
                await self.bot.channels["logs"].send("**Failed to connect to easygTTS for unknown reason.**")
            else:
                await self.bot.channels["logs"].send("**Rate limit still in place, waiting another hour.**")

            await asyncio.sleep(3601)

        await self.bot.channels["logs"].send("**Swapping back to easygTTS**")
        self.bot.blocked = False

    @utils.decos.handle_errors
    async def _send_fallback(self):
        guild = self.guild
        if not guild or guild.unavailable:
            return

        channel = cast(discord.TextChannel, guild.get_channel(self.linked_channel))
        if not channel:
            return

        permissions = channel.permissions_for(guild.me)
        if permissions.send_messages and permissions.embed_links:
            await channel.send(embed=await self._get_embed())

    async def _get_embed(self):
        prefix = self.prefix or (await self.bot.settings.get(self.guild, ["prefix"]))[0]

        return discord.Embed(
            title="TTS Bot has been blocked by Google",
            description=cleandoc(f"""
            During this temporary block, voice has been swapped to a worse quality voice.
            If you want to avoid this, consider TTS Bot Premium, which you can get by donating via Patreon: `{prefix}donate`
            """)
        ).set_footer(text="You can join the support server for more info: discord.gg/zWPWwQC")


    # Helper functions
    def _after_player(self, exception: Optional[Exception]) -> Sequence[Any]:
        # This func runs in a seperate thread and has to do awaiting,
        # also we want to get back to the main thread to fix race conditions.
        coro = self._after_player_coro(exception)
        return utils.to_async(coro, self.bot.loop)

    async def _after_player_coro(self, exception: Optional[Exception]) -> Sequence[Any]:
        exceptions = [exception] or []
        try:
            self.currently_playing.set()
        except Exception as error:
            exceptions.append(error)

        return await asyncio.gather(*(
            self.bot.on_error("play_audio", exception)
            for exception in exceptions
        ))