import asyncio
import collections
import traceback
from typing import Optional

from interactions.ext.wait_for import wait_for

from interactions import Client, Message

EmojiSettings = collections.namedtuple("EmojiSettings", "start back forward end close")

EMOJI_DEFAULT = EmojiSettings(
    start="\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}",
    back="\N{BLACK LEFT-POINTING TRIANGLE}",
    forward="\N{BLACK RIGHT-POINTING TRIANGLE}",
    end="\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}",
    close="\N{BLACK SQUARE FOR STOP}",
)


class Paginator:
    """A class that aids in paginating code blocks for Discord messages.

    .. container:: operations

        .. describe:: len(x)

            Returns the total number of characters in the paginator.

    Attributes
    -----------
    prefix: :class:`str`
        The prefix inserted to every page. e.g. three backticks.
    suffix: :class:`str`
        The suffix appended at the end of every page. e.g. three backticks.
    max_size: :class:`int`
        The maximum amount of codepoints allowed in a page.
    linesep: :class:`str`
        The character string inserted between lines. e.g. a newline character.
            .. versionadded:: 1.7
    """

    def __init__(self, prefix="```", suffix="```", max_size=2000, linesep="\n"):
        self.prefix = prefix
        self.suffix = suffix
        self.max_size = max_size
        self.linesep = linesep
        self.clear()

    def clear(self):
        """Clears the paginator to have no pages."""
        if self.prefix is not None:
            self._current_page = [self.prefix]
            self._count = len(self.prefix) + self._linesep_len  # prefix + newline
        else:
            self._current_page = []
            self._count = 0
        self._pages = []

    @property
    def _prefix_len(self):
        return len(self.prefix) if self.prefix else 0

    @property
    def _suffix_len(self):
        return len(self.suffix) if self.suffix else 0

    @property
    def _linesep_len(self):
        return len(self.linesep)

    def add_line(self, line="", *, empty=False):
        """Adds a line to the current page.

        If the line exceeds the :attr:`max_size` then an exception
        is raised.

        Parameters
        -----------
        line: :class:`str`
            The line to add.
        empty: :class:`bool`
            Indicates if another empty line should be added.

        Raises
        ------
        RuntimeError
            The line was too big for the current :attr:`max_size`.
        """
        max_page_size = (
            self.max_size - self._prefix_len - self._suffix_len - 2 * self._linesep_len
        )
        if len(line) > max_page_size:
            raise RuntimeError("Line exceeds maximum page size %s" % (max_page_size))

        if (
            self._count + len(line) + self._linesep_len
            > self.max_size - self._suffix_len
        ):
            self.close_page()

        self._count += len(line) + self._linesep_len
        self._current_page.append(line)

        if empty:
            self._current_page.append("")
            self._count += self._linesep_len

    def close_page(self):
        """Prematurely terminate a page."""
        if self.suffix is not None:
            self._current_page.append(self.suffix)
        self._pages.append(self.linesep.join(self._current_page))

        if self.prefix is not None:
            self._current_page = [self.prefix]
            self._count = len(self.prefix) + self._linesep_len  # prefix + linesep
        else:
            self._current_page = []
            self._count = 0

    def __len__(self):
        total = sum(len(p) for p in self._pages)
        return total + self._count

    @property
    def pages(self):
        """List[:class:`str`]: Returns the rendered list of pages."""
        # we have more than just the prefix in our current page
        if len(self._current_page) > (0 if self.prefix is None else 1):
            self.close_page()
        return self._pages

    def __repr__(self):
        fmt = "<Paginator prefix: {0.prefix!r} suffix: {0.suffix!r} linesep: {0.linesep!r} max_size: {0.max_size} count: {0._count}>"
        return fmt.format(self)


class WrappedPaginator(Paginator):
    """
    A paginator that allows automatic wrapping of lines should they not fit.
    This is useful when paginating unpredictable output,
    as it allows for line splitting on big chunks of data.
    Delimiters are prioritized in the order of their tuple.
    Parameters
    -----------
    wrap_on: tuple
        A tuple of wrapping delimiters.
    include_wrapped: bool
        Whether to include the delimiter at the start of the new wrapped line.
    force_wrap: bool
        If this is True, lines will be split at their maximum points should trimming not be possible
        with any provided delimiter.
    """

    def __init__(
        self,
        *args,
        wrap_on=("\n", " "),
        include_wrapped=True,
        force_wrap=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.wrap_on = wrap_on
        self.include_wrapped = include_wrapped
        self.force_wrap = force_wrap

    def add_line(self, line="", *, empty=False):
        true_max_size = self.max_size - self._prefix_len - self._suffix_len - 2
        original_length = len(line)

        while len(line) > true_max_size:
            search_string = line[: true_max_size - 1]
            wrapped = False

            for delimiter in self.wrap_on:
                position = search_string.rfind(delimiter)

                if position > 0:
                    super().add_line(line[:position], empty=empty)
                    wrapped = True

                    if self.include_wrapped:
                        line = line[position:]
                    else:
                        line = line[position + len(delimiter) :]

                    break

            if not wrapped:
                if not self.force_wrap:
                    raise ValueError(
                        f"Line of length {original_length} had sequence of {len(line)} characters"
                        f" (max is {true_max_size}) that WrappedPaginator could not wrap with"
                        f" delimiters: {self.wrap_on}"
                    )

                super().add_line(line[: true_max_size - 1])
                line = line[true_max_size - 1 :]
        super().add_line(line, empty=empty)


class PaginatorInterface:  # pylint: disable=too-many-instance-attributes
    """
    A message and reaction based interface for paginators.
    This allows users to interactively navigate the pages of a Paginator, and supports live output.
    An example of how to use this with a standard Paginator:
    .. code:: python3
        from discord.ext import commands
        from jishaku.paginators import PaginatorInterface
        # In a command somewhere...
            # Paginators need to have a reduced max_size to accommodate the extra text added by the interface.
            paginator = commands.Paginator(max_size=1900)
            # Populate the paginator with some information
            for line in range(100):
                paginator.add_line(f"Line {line + 1}")
            # Create and send the interface.
            # The 'owner' field determines who can interact with this interface. If it's None, anyone can use it.
            interface = PaginatorInterface(ctx.bot, paginator, owner=ctx.author)
            await interface.send_to(ctx)
            # send_to creates a task and returns control flow.
            # It will raise if the interface can't be created, e.g., if there's no reaction permission in the channel.
            # Once the interface has been sent, line additions have to be done asynchronously, so the interface can be updated.
            await interface.add_line("My, the Earth sure is full of things!")
            # You can also check if it's closed using the 'closed' property.
            if not interface.closed:
                await interface.add_line("I'm still here!")
    """

    # noinspection PyTypeChecker
    def __init__(self, bot: Client, paginator: Paginator, **kwargs):
        if not isinstance(paginator, Paginator):
            raise TypeError("paginator must be a Paginator instance")

        self._display_page = 0

        self.bot = bot

        self.message: Optional[Message] = None
        self.paginator = paginator

        self.owner = kwargs.pop("owner", None)
        self.emojis = kwargs.pop("emoji", EMOJI_DEFAULT)
        self.timeout = kwargs.pop("timeout", 7200)
        self.delete_message = kwargs.pop("delete_message", False)

        self.sent_page_reactions = False

        self.task: asyncio.Task = None
        self.send_lock: asyncio.Event = asyncio.Event()

        self.close_exception: Exception = None

        if self.page_size > self.max_page_size:
            raise ValueError(
                f"Paginator passed has too large of a page size for this interface. "
                f"({self.page_size} > {self.max_page_size})"
            )

    @property
    def pages(self):
        """
        Returns the paginator's pages without prematurely closing the active page.
        """
        # protected access has to be permitted here to not close the paginator's pages

        # pylint: disable=protected-access
        paginator_pages = list(self.paginator._pages)
        if len(self.paginator._current_page) > 1:
            paginator_pages.append(
                "\n".join(self.paginator._current_page)
                + "\n"
                + (self.paginator.suffix or "")
            )
        # pylint: enable=protected-access

        return paginator_pages

    @property
    def page_count(self):
        """
        Returns the page count of the internal paginator.
        """

        return len(self.pages)

    @property
    def display_page(self):
        """
        Returns the current page the paginator interface is on.
        """

        self._display_page = max(0, min(self.page_count - 1, self._display_page))
        return self._display_page

    @display_page.setter
    def display_page(self, value):
        """
        Sets the current page the paginator is on. Automatically pushes values inbounds.
        """

        self._display_page = max(0, min(self.page_count - 1, value))

    max_page_size = 2000

    @property
    def page_size(self) -> int:
        """
        A property that returns how large a page is, calculated from the paginator properties.
        If this exceeds `max_page_size`, an exception is raised upon instantiation.
        """
        page_count = self.page_count
        return self.paginator.max_size + len(f"\nPage {page_count}/{page_count}")

    @property
    def send_kwargs(self) -> dict:
        """
        A property that returns the kwargs forwarded to send/edit when updating the page.
        As this must be compatible with both `discord.TextChannel.send` and `discord.Message.edit`,
        it should be a dict containing 'content', 'embed' or both.
        """

        display_page = self.display_page
        page_num = f"\nPage {display_page + 1}/{self.page_count}"
        content = self.pages[display_page] + page_num
        return {"content": content}

    async def add_line(self, *args, **kwargs):
        """
        A proxy function that allows this PaginatorInterface to remain locked to the last page
        if it is already on it.
        """

        display_page = self.display_page
        page_count = self.page_count

        self.paginator.add_line(*args, **kwargs)

        new_page_count = self.page_count

        if display_page + 1 == page_count:
            # To keep position fixed on the end, update position to new last page and update message.
            self._display_page = new_page_count

        # Unconditionally set send lock to try and guarantee page updates on unfocused pages
        self.send_lock.set()

    async def send_to(self, destination: "discord.abc.Messageable"):
        """
        Sends a message to the given destination with this interface.
        This automatically creates the response task for you.
        """

        self.message = await destination.send(**self.send_kwargs)

        # add the close reaction
        await self.message.add_reaction(self.emojis.close)

        self.send_lock.set()

        if self.task:
            self.task.cancel()

        self.task = self.bot._loop.create_task(self.wait_loop())

        # if there is more than one page, and the reactions haven't been sent yet, send navigation emotes
        if not self.sent_page_reactions and self.page_count > 1:
            await self.send_all_reactions()

        return self

    async def send_all_reactions(self):
        """
        Sends all reactions for this paginator, if any are missing.
        This method is generally for internal use only.
        """

        for emoji in filter(None, self.emojis):
            try:
                await self.message.add_reaction(emoji)
            except discord.NotFound:
                # the paginator has probably already been closed
                break
        self.sent_page_reactions = True

    @property
    def closed(self):
        """
        Is this interface closed?
        """

        return False if not self.task else self.task.done()

    async def send_lock_delayed(self):
        """
        A coroutine that returns 1 second after the send lock has been released
        This helps reduce release spam that hits rate limits quickly
        """

        gathered = await self.send_lock.wait()
        self.send_lock.clear()
        await asyncio.sleep(1)
        return gathered

    async def wait_loop(self):  # pylint: disable=too-many-branches, too-many-statements
        """
        Waits on a loop for reactions to the message. This should not be called manually - it is handled by `send_to`.
        """

        start, back, forward, end, close = self.emojis

        def check(payload: discord.RawReactionActionEvent):
            """
            Checks if this reaction is related to the paginator interface.
            """

            owner_check = not self.owner or payload.user_id == self.owner.id

            emoji = payload.emoji
            if isinstance(emoji, discord.PartialEmoji) and emoji.is_unicode_emoji():
                emoji = emoji.name

            tests = (
                owner_check,
                payload.message_id == self.message.id,
                emoji,
                emoji in self.emojis,
                payload.user_id != self.bot.me.user.id,
            )

            return all(tests)

        task_list = [
            self.bot._loop.create_task(coro)
            for coro in {
                wait_for(self.bot, "on_reaction_add", check=check),
                wait_for(self.bot, "on_reaction_remove", check=check),
                self.send_lock_delayed(),
            }
        ]

        try:  # pylint: disable=too-many-nested-blocks
            last_kwargs = None

            while not self.bot._loop.is_closed():
                done, _ = await asyncio.wait(
                    task_list, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED
                )

                if not done:
                    raise asyncio.TimeoutError

                for task in done:
                    task_list.remove(task)
                    payload = task.result()

                    if isinstance(payload, discord.RawReactionActionEvent):
                        emoji = payload.emoji
                        if (
                            isinstance(emoji, discord.PartialEmoji)
                            and emoji.is_unicode_emoji()
                        ):
                            emoji = emoji.name

                        if emoji == close:
                            await self.message.delete()
                            return

                        if emoji == start:
                            self._display_page = 0
                        elif emoji == end:
                            self._display_page = self.page_count - 1
                        elif emoji == back:
                            self._display_page -= 1
                        elif emoji == forward:
                            self._display_page += 1

                        if payload.event_type == "REACTION_ADD":
                            task_list.append(
                                self.bot._loop.create_task(
                                    self.bot.wait_for("raw_reaction_add", check=check)
                                )
                            )
                        elif payload.event_type == "REACTION_REMOVE":
                            task_list.append(
                                self.bot._loop.create_task(
                                    self.bot.wait_for(
                                        "raw_reaction_remove", check=check
                                    )
                                )
                            )
                    else:
                        # Send lock was released
                        task_list.append(
                            self.bot._loop.create_task(self.send_lock_delayed())
                        )

                if not self.sent_page_reactions and self.page_count > 1:
                    self.bot._loop.create_task(self.send_all_reactions())
                    self.sent_page_reactions = True  # don't spawn any more tasks

                if self.send_kwargs != last_kwargs:
                    try:
                        await self.message.edit(**self.send_kwargs)
                    except Exception:
                        traceback.format_exc()

                    last_kwargs = self.send_kwargs

        except (asyncio.CancelledError, asyncio.TimeoutError) as exception:
            self.close_exception = exception

            # if self.bot.is_closed():
            #     # Can't do anything about the messages, so just close out to avoid noisy error
            #     return
            try:
                if self.delete_message:
                    return await self.message.delete()

                for emoji in filter(None, self.emojis):
                    try:
                        self.bot._http.remove_self_reaction(
                            int(self.message.channel_id), int(self.message.id), emoji
                        )
                    except Exception:
                        traceback.format_exc()
            except:
                pass

        finally:
            for task in task_list:
                task.cancel()
