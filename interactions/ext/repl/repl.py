import asyncio
import io
import traceback
from contextlib import redirect_stdout
from functools import partial
from typing import Dict, Union

from interactions.ext.wait_for import setup as setup_wait_for
from interactions.ext.wait_for import wait_for

from interactions import Channel, Extension, Guild, Message, extension_listener

from .jsk import (AsyncCodeExecutor, Scope, codeblock_converter,
                  jsk_python_result_handling)


class ReplExtension(Extension):
    def __init__(self, client):
        self.sessions: set = set()

    @extension_listener("on_message_create")
    async def on_message(self, msg: Message):
        if int(msg.author.id) != int(self.client.me.owner.id):
            return

        if msg.content not in (
            f"<@{self.client.me.id}> repl",
            f"<@!{self.client.me.id}> repl",
        ):
            return

        if msg.channel_id in self.sessions:
            return await (await msg.get_channel()).send(
                "There is already an active repl session!"
            )

        self.sessions.add(msg.channel_id)
        try:
            await self.start_repl(msg)
        except asyncio.TimeoutError:
            await (await msg.get_channel()).send("Timed out")
        finally:
            self.sessions.remove(msg.channel_id)

    async def gather_env(self, msg: Message) -> dict:
        msg._client = self.client._http
        channel = await msg.get_channel()
        guild = await msg.get_guild()

        return {
            "message": msg,
            "channel": channel,
            "guild": guild,
            "author": msg.member or msg.author,
            "bot": self.client,
            "client": self.client,
            "_": None,
        }

    async def start_repl(self, msg: Message):

        env = await self.gather_env(msg)
        scope = Scope()

        send = partial(env["channel"].send)
        author = env["author"]

        def check(_message: Message) -> bool:
            _message_author = _message.member or _message.author
            is_codeblock = _message.content.startswith(
                "`"
            ) and _message.content.endswith("`")

            return (
                int(msg.channel_id) == int(_message.channel_id)
                and int(author.id) == int(_message_author.id)
                and is_codeblock
            )

        await msg.reply("Starting repl")

        while True:  # I've been working on a code thing
            code = codeblock_converter(
                (
                    await wait_for(
                        self.client,
                        "on_message_create",
                        check=check,
                        timeout=10.0 * 60.0,
                    )
                ).content
            )

            if code.content in ("quit", "exit", "exit()"):
                return await msg.reply("Exiting.")

            stdout = io.StringIO()

            try:
                with redirect_stdout(stdout):
                    executor = AsyncCodeExecutor(code.content, scope, arg_dict=env)
                    async for result in executor:
                        if result is None:
                            continue

                        env["_"] = result

                        await jsk_python_result_handling(msg, result, self.client)

            except Exception as e:
                value = stdout.getvalue()
                val_fmt = f"`stdout`:\n```py\n{value}\n```" if value else None
                exc_fmt = f"Traceback:\n```py\n{traceback.format_exc()}\n```"

                if val_fmt is not None:
                    if len(val_fmt) > 2000:
                        await send("Content is too big to be sent")
                    else:
                        await send(val_fmt)

                if len(exc_fmt) > 2000:
                    await send("Traceback is too big to be sent\n"
                               f"{e.__class__.__name__}: {e.__str__()}")
                else:
                    await send(exc_fmt)

            else:
                value = stdout.getvalue()
                val_fmt = f"`stdout`:\n```py\n{value}\n```" if value else None

                if val_fmt is not None:
                    if len(val_fmt) > 2000:
                        await send("Content is too big to be sent")
                    else:
                        await send(val_fmt)


def setup(client):
    setup_wait_for(client)
    ReplExtension(client)
