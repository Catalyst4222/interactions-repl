# -*- coding: utf-8 -*-

"""
jishaku.python.compilation
~~~~~~~~~~~~~~~~~~~~~~~~

Constants, functions and classes related to classifying, compiling and executing Python code.

:copyright: (c) 2021 Devon (Gorialis) R
:license: MIT, see LICENSE for more details.

"""

import ast
import asyncio
import functools
import inspect
import io
import linecache

import import_expression

from interactions import Client, Embed, Message

from ..paginators import PaginatorInterface, WrappedPaginator
from .jsk_functools import AsyncSender
from .scope import Scope
from .walkers import KeywordTransformer

CORO_CODE = """
async def _repl_coroutine({{0}}):
    import asyncio
    from importlib import import_module as {0}

    import aiohttp
    import interactions

    try:
        import jishaku
    except ImportError:
        jishaku = None  # keep working even if in panic recovery mode

    try:
        pass
    finally:
        _async_executor.scope.globals.update(locals())
""".format(
    import_expression.constants.IMPORTER
)


def wrap_code(code: str, args: str = "") -> ast.Module:
    """
    Compiles Python code into an async function or generator,
    and automatically adds return if the function body is a single evaluation.
    Also adds inline import expression support.
    """

    user_code = import_expression.parse(code, mode="exec")
    mod = import_expression.parse(CORO_CODE.format(args), mode="exec")

    definition = mod.body[-1]  # async def ...:
    assert isinstance(definition, ast.AsyncFunctionDef)

    try_block = definition.body[-1]  # try:
    assert isinstance(try_block, ast.Try)

    try_block.body.extend(user_code.body)

    ast.fix_missing_locations(mod)

    KeywordTransformer().generic_visit(try_block)

    last_expr = try_block.body[-1]

    # if the last part isn't an expression, ignore it
    if not isinstance(last_expr, ast.Expr):
        return mod

    # if the last expression is not a yield
    if not isinstance(last_expr.value, ast.Yield):
        # copy the value of the expression into a yield
        yield_stmt = ast.Yield(last_expr.value)
        ast.copy_location(yield_stmt, last_expr)
        # place the yield into its own expression
        yield_expr = ast.Expr(yield_stmt)
        ast.copy_location(yield_expr, last_expr)

        # place the yield where the original expression was
        try_block.body[-1] = yield_expr

    return mod


class AsyncCodeExecutor:  # pylint: disable=too-few-public-methods
    """
    Executes/evaluates Python code inside of an async function or generator.

    Example
    -------

    .. code:: python3

        total = 0

        # prints 1, 2 and 3
        async for x in AsyncCodeExecutor('yield 1; yield 2; yield 3'):
            total += x
            print(x)

        # prints 6
        print(total)
    """

    __slots__ = ("args", "arg_names", "code", "loop", "scope", "source")

    def __init__(
        self,
        code: str,
        scope: Scope = None,
        arg_dict: dict = None,
        loop: asyncio.BaseEventLoop = None,
    ):
        self.args = [self]
        self.arg_names = ["_async_executor"]

        if arg_dict:
            for key, value in arg_dict.items():
                self.arg_names.append(key)
                self.args.append(value)

        self.source = code
        self.code = wrap_code(code, args=", ".join(self.arg_names))
        self.scope = scope or Scope()
        self.loop = loop or asyncio.get_event_loop()

    def __aiter__(self):
        exec(
            compile(self.code, "<python>", "exec"),
            self.scope.globals,
            self.scope.locals,
        )  # pylint: disable=exec-used
        func_def = (
            self.scope.locals.get("_repl_coroutine")
            or self.scope.globals["_repl_coroutine"]
        )

        return self.traverse(func_def)

    async def traverse(self, func):
        """
        Traverses an async function or generator, yielding each result.

        This function is private. The class should be used as an iterator instead of using this method.
        """

        try:
            if inspect.isasyncgenfunction(func):
                async for send, result in AsyncSender(func(*self.args)):
                    send((yield result))
            else:
                yield await func(*self.args)
        except Exception:  # pylint: disable=broad-except
            # Falsely populate the linecache to make the REPL line appear in tracebacks
            linecache.cache["<python>"] = (
                len(self.source),  # Source length
                None,  # Time modified (None bypasses expunge)
                [line + "\n" for line in self.source.splitlines()],  # Line list
                "<python>",  # 'True' filename
            )

            raise


async def jsk_python_result_handling(
    msg: Message, result, bot: Client, blacklist: dict
):  # pylint: disable=too-many-return-statements
    """
    Determines what is done with a result when it comes out of jsk py.
    This allows you to override how this is done without having to rewrite the command itself.
    What you return is what gets stored in the temporary _ variable.
    """
    channel = await msg.get_channel()
    send = functools.partial(
        channel.send,
    )

    if isinstance(result, Message):
        return await send(f"<Message <{result.id}>>")

    # if isinstance(result, discord.File):
    #     return await ctx.send(file=result)

    if isinstance(result, Embed):
        return await send(embeds=result)

    if isinstance(result, PaginatorInterface):
        return await result.send_to(channel)

    if not isinstance(result, str):
        # repr all non-strings
        result = repr(result)

    for before, after in blacklist.items():
        # .replace doesn't seem to work?
        temp = result.split(before)
        result = after.join(temp)

    # Eventually the below handling should probably be put somewhere else
    if len(result) <= 1900:
        if result.strip() == "":
            result = "\u200b"

        return await send(
            "```py\n" + result.replace(msg._client.token, "[token omitted]") + "\n```"
        )

    # inconsistency here, results get wrapped in codeblocks when they are too large
    #  but don't if they're not. probably not that bad, but noting for later review
    paginator = WrappedPaginator(prefix="```py", suffix="```", max_size=1985)

    paginator.add_line(result)

    interface = PaginatorInterface(bot, paginator, owner=ctx.author)
    return await interface.send_to(channel)
