"""F10-4: the page tree, edited as intent.

A page manipulation is the kind of edit that can quietly destroy a document, so
every one here is an :class:`~pdfar.edits.Edit`: it says what the user asked for,
runs against a throwaway copy, and is adopted only when it returns without
raising.  Nothing in this module touches the live document.

Five items so far, in the order the roadmap needed them: turning a page, which is
where the page-tree machinery was proved; taking pages out, which is where it
starts to matter that every number above the cut means something else afterwards;
settling the tree, which is the one every reorder waits for — a page that does not
say how big it is has to say so before anything decides which node it hangs from;
moving pages, which is that reorder, and the first edit whose *only* effect is
the numbering: nothing is added and nothing is taken away, so every page number
held outside the document has to be re-aimed at once and a map that clamps — the
delete's answer — would be a lie; and reversing a pick, which is the same walk run
against a permutation of its own, and the first reorder that leaves the sheets it
was not asked about wearing the numbers they had.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .edits import Edit, EditError, Scope


def contiguous_runs(pages: Iterable[int]) -> Tuple[Tuple[int, int], ...]:
    """The blocks of neighbouring pages a set is made of, in ascending order.

    A page-tree operation costs a rebuild of the whole tree, not a cost per page:
    measured on the 540-page book, one page out and a hundred out both took 45 ms,
    while removing the same hundred one at a time took 2055 ms.  So a pick of a
    block is asked for as one run, and only a scattered pick pays per page.
    """
    runs: List[List[int]] = []
    for page in sorted(set(pages)):
        if runs and runs[-1][1] == page - 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return tuple((start, end) for start, end in runs)


def shifted_index(page: int, deleted: Iterable[int], count: int) -> int:
    """Where the sheet numbered *page* sits once *deleted* leave a *count*-page book.

    A delete does not only take pages out: every page above the cut takes the number
    of the one below it, so any page number a reader, the strip's pick or a bookmark
    was holding means another sheet afterwards.  That is what this answers.

    A page that went itself is answered with the sheet that took its place, which is
    where the reading carries on from; the clamp at the end is for the last page of
    the document going, when nothing arrives to fill its number.
    """
    gone = set(deleted)
    below = sum(1 for index in gone if index < page)
    return max(0, min(page - below, count - len(gone) - 1))


@dataclass(frozen=True)
class RotatePages(Edit):
    """Turn the named pages by *delta* degrees, into the file.

    The delta is a quarter turn or several, because that is all PDF has: an
    angle that is not a multiple of 90 is refused rather than handed to MuPDF,
    which folds it silently to *no turn at all* — a document that looks
    unchanged after a command that promised to change it.

    A page's turn is an **inheritable** page-tree attribute: a ``/Rotate`` on any
    ancestor ``/Pages`` node applies to every page below it unless the page says
    otherwise.  That is why the edit writes the page's own node and why the angle
    it writes is measured from ``page.rotation`` — which is the turn the page is
    *displayed* at, inheritance included — rather than from whatever the page node
    happens to hold.  Writing the sum on the page leaves the ancestors alone, so
    the pages beside the one asked for keep the turn they had.
    """

    pages: Tuple[int, ...]
    delta: int = 90

    scope = Scope.STRUCTURE

    @property
    def label(self) -> str:
        if len(self.pages) == 1:
            return "Rotate Page"
        return f"Rotate {len(self.pages)} Pages"

    def apply(self, doc: fitz.Document) -> None:
        if not self.pages:
            raise EditError("No page was chosen to rotate")
        if self.delta % 90:
            raise EditError(
                f"A page can only be turned by a multiple of 90°, not {self.delta}°")
        count = doc.page_count
        for index in self.pages:
            # Every index is checked before a single one is written: a list that
            # names a page this document does not have is refused whole, so the
            # pages that were valid are not turned on the way out.
            if not 0 <= index < count:
                raise EditError(
                    f"Page {index + 1} is not in this document, which has "
                    f"{count} pages")
        for index in dict.fromkeys(self.pages):
            # A page named twice is one page turned once; the intent is which
            # pages, not how many times the list said so.
            page = doc.load_page(index)
            page.set_rotation((page.rotation + self.delta) % 360)


@dataclass(frozen=True)
class DeletePages(Edit):
    """Take the named pages out of the document, and their marks with them.

    MuPDF removes a page's own annotations with it and leaves the survivors on the
    pages they were on, so the delete is contained — but every page *above* the cut
    takes the number of the one below it, and that is why :attr:`changed` says
    nothing while :attr:`pages` keeps the list the user named.

    Two refusals that are not MuPDF's:

    * a **negative** index.  ``delete_page(-1)`` is accepted and removes the *last*
      page, because MuPDF counts a negative index from the end; the range form does
      refuse one, so what checking it here buys is a sentence that names the page
      and a page tree that was never cut.
    * a request that would leave **no pages**.  MuPDF takes the last one out without
      comment and raises ``cannot save with zero pages`` at the save, well after the
      edit was accepted, undoable and shown to the user as done.
    """

    pages: Tuple[int, ...]

    scope = Scope.STRUCTURE

    @property
    def changed(self) -> Optional[Tuple[int, ...]]:
        """Every page from the cut onwards, which is not worth pretending to know.

        The pages that go are not the pages that move: after page 2 of five goes,
        the marks, thumbnails and outline rows numbered 3 and 4 name different
        sheets than they did.  Naming the deleted indices would let a per-page
        cache keep those entries under numbers that no longer reach them.
        """
        return None

    def page_order(self, before_count: int) -> Tuple[int, ...]:
        """The survivors, in the order they keep.

        ``changed`` can only say "everything from the cut onwards" because it
        answers *which pages to re-read*; this answers *where each page went*,
        which is what a bookmark held outside the document needs: not how many
        pages moved, but which number its own sheet wears now.
        """
        named = set(self.pages)
        return tuple(index for index in range(before_count) if index not in named)

    @property
    def label(self) -> str:
        if len(self.pages) == 1:
            return "Delete Page"
        return f"Delete {len(self.pages)} Pages"

    def apply(self, doc: fitz.Document) -> None:
        if not self.pages:
            raise EditError("No page was chosen to delete")
        count = doc.page_count
        word = "page" if count == 1 else "pages"
        # Checked whole before anything is removed, so a list that names a page this
        # document does not have takes nothing out on the way out.
        for index in self.pages:
            if not 0 <= index < count:
                raise EditError(
                    f"Page {index + 1} is not in this document, which has "
                    f"{count} {word}")
        named = set(self.pages)
        if len(named) >= count:
            raise EditError(
                f"This document is {count} {word} and all of it is being deleted; "
                f"a page is needed to save it")
        # Highest run first: a delete shifts the numbers above it, so working up
        # from the front would spend the list on the wrong pages.
        for start, end in sorted(contiguous_runs(named), reverse=True):
            doc.delete_pages(start, end)
        self._drop_dead_outline_entries(doc)

    @staticmethod
    def _drop_dead_outline_entries(doc: fitz.Document) -> None:
        """Prune the outline rows MuPDF left pointing at nothing.

        An entry whose page went reads ``page == -1`` with its destination demoted
        to kind 0, which the sidebar can draw but no click can follow; the entries
        that landed on survivors are already shifted, so they need nothing.  The
        tree is rewritten only when something actually died, because ``set_toc``
        writes every entry again — new objects for rows the user did not touch.

        Removing a row can also leave the *shape* invalid, and MuPDF refuses to
        write such a tree rather than repairing it: a first entry deeper than level 1
        (``hierarchy level of item 0 must be 1``) once the row above it is gone, and a
        step of two levels (``bad hierarchy level in row N``) once the row in between
        is.  Both are answered by one clamp: no entry may sit more than one level
        below the entry before it, which lifts an orphan without flattening anything
        that was already sound.
        """
        entries = doc.get_toc(simple=False)
        live = [entry for entry in entries if entry[2] >= 1]
        if len(live) == len(entries):
            return
        previous = 0
        for entry in live:
            entry[0] = previous = max(1, min(entry[0], previous + 1))
        doc.set_toc(live)



#: The four keys a ``/Page`` is allowed to leave to an ancestor (ISO 32000-1
#: Table 30).  Three of them are a value the page can hold for itself; the
#: fourth is a dictionary that resolves *per sub-dictionary*, which is why this
#: module copies three and refuses the fourth rather than pretending they work
#: the same way.
INHERITABLE_KEYS = ("MediaBox", "CropBox", "Rotate", "Resources")

#: The inheritable keys a page can be given its own copy of, and be done with it.
PUSHABLE_KEYS = ("MediaBox", "CropBox", "Rotate")

#: What a node may hold simply for being a node in the tree.
TREE_KEYS = ("Type", "Parent", "Kids", "Count")


def _pages_root(doc: fitz.Document) -> int:
    """The ``/Pages`` node the catalog names, or a refusal to go further."""
    value = doc.xref_get_key(doc.pdf_catalog(), "Pages")[1]
    found = re.match(r"\s*(\d+)\s+0\s+R", value)
    if not found:
        raise EditError(
            f"This document’s catalog does not name a page tree (got {value!r})")
    return int(found.group(1))


def _stated(doc: fitz.Document, xref: int, key: str) -> Optional[str]:
    """The value *xref* holds for *key*, or ``None`` for "not stated here".

    MuPDF answers ``null`` for a key that is absent and for one written as an
    explicit ``null``, and the standard treats both the same way: ask above.
    """
    value = doc.xref_get_key(xref, key)[1]
    return None if value == "null" else value


def _walk_tree(doc: fitz.Document, xref: int, path: "frozenset[int]",
               sheets: List[int], reached: List[int], depth: int = 0) -> None:
    """Collect every page the tree reaches, and every node above them.

    *sheets* keeps a page the tree lists twice, because ``page_count`` counts it
    twice and the two have to agree for anything to be settled.  *path* is the chain
    from the root to here — not every node already seen — so a sheet named twice is
    a document and a node that names itself is a refusal.  *reached* is the nodes
    themselves, which the settle uses to ask whether a key is stated anywhere at all:
    a move re-parents by ``/Parent``, so a node the pages currently walk *past* is one
    they can land under — see the ``live`` set in :meth:`SettlePageTree.apply`.

    A ``/Pages`` node with an **empty** ``/Kids`` array contributes nothing and is
    walked past: that is the shape ``move_page`` leaves behind, because a page that
    moves is re-parented into the node it lands in and the node it came from keeps
    its dictionary with no children.  Refusing it would refuse the document the
    previous edit just made.  A node that states no ``/Kids`` at all is a different
    thing — MuPDF counts it as a page — and is refused.

    This says what the *tree* reaches.  What a page inherits is a different walk,
    because the renderer asks the page's own ``/Parent`` links: see
    :func:`_parent_chain`.
    """
    if xref in path:
        raise EditError(
            f"Object {xref} of the page tree is its own ancestor; nothing is "
            f"settled on a tree that loops")
    try:
        keys: Sequence[str] = list(doc.xref_get_keys(xref))
        kind = doc.xref_get_key(xref, "Type")[1]
        kids = doc.xref_get_key(xref, "Kids")[1]
    except Exception as exc:            # noqa: BLE001 - reported to the user
        raise EditError(
            f"The page tree cannot be read at object {xref}: {exc}") from exc
    if kind != "/Pages" and "Kids" not in keys:
        if kind != "/Page" and not keys:
            # A ``/Kids`` entry that resolves to no dictionary at all — an integer,
            # a stream, nothing.  Counting it as a sheet would leave the count
            # agreeing with the document and hand the caller a page it cannot write.
            raise EditError(
                f"Object {xref} of the page tree is not a page, so this "
                f"document’s page tree cannot be settled")
        sheets.append(xref)
        return
    if depth:
        reached.append(xref)
    children = [int(found) for found in re.findall(r"(\d+)\s+0\s+R", kids)]
    if not children:
        if kids == "null":
            raise EditError(
                f"Node {xref} of the page tree states no /Kids, so it has no "
                f"children to name")
        return
    below = path | {xref}
    for child in children:
        _walk_tree(doc, child, below, sheets, reached, depth + 1)


def _parent_chain(doc: fitz.Document, page: int, root: int) -> Tuple[int, ...]:
    """The nodes *page* inherits from, nearest first, the way the renderer asks.

    MuPDF resolves an inherited attribute by walking the page's own ``/Parent``
    links, not by walking the tree down from the root, and the two agree only while
    a producer kept them consistent.  Measured on a book whose ``/Kids`` had been
    rewritten without touching the pages' ``/Parent``: every sheet sat under a node
    stating ``/MediaBox [0 0 595 842]`` and ``/Rotate 90``, and MuPDF drew it
    612×792 and unturned, because the node ``/Parent`` named — the root — states
    neither.  Copying what the tree says onto that page would have handed it a
    geometry it never had, which is the opposite of what this step is for.

    So the tree walk decides *which objects are pages* and this decides *what each
    of them was being shown*.  A chain that stops short of the root is not a
    refusal: whatever it resolves to today is what the reader sees, and freezing
    that is exactly the point.
    """
    chain: List[int] = []
    seen = {page}
    value = _stated(doc, page, "Parent")
    while value is not None:
        found = re.match(r"\s*(\d+)\s+0\s+R", value)
        if not found:
            break
        above = int(found.group(1))
        if above in seen:
            raise EditError(
                f"Object {page}’s /Parent chain reaches {above} twice; nothing is "
                f"settled on a document whose pages inherit from themselves")
        chain.append(above)
        if above == root:
            break
        seen.add(above)
        try:
            value = _stated(doc, above, "Parent")
        except Exception as exc:        # noqa: BLE001 - reported to the user
            raise EditError(
                f"The page tree cannot be read at object {above}: {exc}") from exc
    return tuple(chain)


@dataclass(frozen=True)
class SettlePageTree(Edit):
    """Give every page the box and the turn it was being shown, where it stands.

    A ``/Page`` does not have to say how big it is or how far it is turned:
    ``/MediaBox``, ``/CropBox``, ``/Rotate`` and ``/Resources`` are **inheritable**,
    so a producer that wants five hundred sheets to share one box writes it once on
    the ``/Pages`` node above them, and MuPDF walks that chain every time it draws
    one.  Which makes the reference a page hangs from the place a page *gets its
    geometry from*, and a reorder splices that reference into another node's
    ``/Kids``.  Measured on a three-sheet book with two ancestors, one turned and
    one cropped: moving the cropped sheet to the front made it 400×300 and gave it
    the other node's turn, and moving the turned sheet to the back took its turn
    away and cropped it — with nothing raised, no page count changed and no word
    moved.

    So each page is given, in its own dictionary, the value it was resolving: the
    nearest node that states the key wins, which is the rule the renderer already
    uses, so a page that reads 260×360 before this step reads 260×360 after it.  A
    page whose whole chain omits a key is given the *default* that key resolves to —
    0 for a turn, the media box for a crop — because a page that stated nothing and
    read 0 is exactly as exposed to an ancestor that says 90 as one that inherited 90
    from the node it is leaving.  The tree is left exactly where the producer put it.

    "the value it was resolving" is looked up the page's own way, through
    :func:`_parent_chain`: a tree whose ``/Kids`` and ``/Parent`` links disagree —
    measured on a 540-sheet book whose nodes were rewritten under it — renders by
    ``/Parent``, and copying the tree's values onto those pages *gave* them a box and
    a turn they had never been drawn with.  The walk down from the root still decides
    which objects the document counts as pages, because that is the claim the reorder
    is about.

    Not ``Document.select``, which is the obvious way to ask MuPDF for this and was
    measured and rejected.  ``select`` *builds a sub-PDF* — ``pdf_rearrange_pages2``
    — and it does the push-down correctly, in 47 ms against this step's 441 on a
    540-sheet book.  But the catalog that comes back no longer names what went in:
    measured across the five documents here it dropped ``/Metadata`` — on every one
    of them — plus ``/PageLabels``, ``/PageMode``, ``/Lang``, ``/MarkInfo``,
    ``/ViewerPreferences``, ``/OutputIntents``, ``/OpenAction``, ``/Threads``,
    ``/Info`` and ``/AcroForm``, and took ``/Outlines`` with it on two of the four
    licensed papers.  The loss is still there after a save: on a book carrying one
    form field the file keeps its widget and loses the catalog's ``/AcroForm``, so
    the form is gone while the box to type in stays.  A push-down that costs the
    document's numbering, its XMP and its outline is not a push-down; it is a
    rewrite of everything beside the page tree.

    Two things this therefore does not do:

    * **Copy ``/Resources`` down.**  A resource dictionary inherits *per
      sub-dictionary* — a page ``/Font`` under an ancestor ``/Font`` resolves both,
      measured — so the only faithful copy is a merge, and the page's own dictionary
      is usually an object other pages share.  An *intermediate* node holding one is
      therefore refused by name; the root's is left alone, because every page in the
      document shares the root and no reorder inside one document can change that.
    * **Refuse rather than drop.**  A tree whose pages cannot be counted, a ``/Kids``
      entry that is not a page, a node that states no ``/Kids`` at all and a chain —
      down through ``/Kids`` or up through ``/Parent`` — that comes back around are
      all refused, because the document cannot be described, let alone settled.
    * **Rebuild the tree.**  Nothing here needs it: once a page states its own box
      and turn, the node it hangs from says nothing about how it draws.  And the
      library's way of flattening is the catalog damage above, while a hand-built one
      moves ``/Kids`` and ``/Parent`` behind MuPDF's page-tree cache, which is the
      kind of thing that shows up as a stale page count later rather than as an error
      now.
    """

    scope = Scope.STRUCTURE
    pages = ()

    @property
    def label(self) -> str:
        return "Settle Page Tree"

    def page_order(self, before_count: int) -> Tuple[int, ...]:
        """The identity — which is a statement, not a shrug.

        No sheet moves, so every page number a bookmark, the strip's pick or the
        reader holds is already right.  Saying so with a map rather than with
        ``None`` is what lets those carries *carry*: a step that says nothing is
        read as a tree that changed in a way nobody can describe, and F10-4n ends
        the search highlights on one.
        """
        return tuple(range(before_count))

    def apply(self, doc: fitz.Document) -> None:
        count = doc.page_count
        if count == 0:
            raise EditError("A document with no pages has no page tree to settle")
        root = _pages_root(doc)
        sheets: List[int] = []
        reached: List[int] = []
        _walk_tree(doc, root, frozenset(), sheets, reached)
        if len(sheets) != count:
            raise EditError(
                f"The page tree holds {len(sheets)} pages but the document says "
                f"{count}; nothing is settled on a tree whose pages cannot be counted")
        # Nearest first, in the order the renderer asks: a page holding its own
        # /Rotate 180 under a node that says 90 keeps 180, and a page the tree
        # reaches one way while /Parent reaches another is settled by /Parent.
        chains = [(page, _parent_chain(doc, page, root))
                  for page in dict.fromkeys(sheets)]
        # Only the nodes a page actually asks through.  A node the tree still
        # reaches — one a move just emptied, or one a producer left off every
        # /Parent chain — states geometry that nothing resolves any more, and
        # refusing it would refuse the document the previous edit made.
        nodes = {root}
        for _, chain in chains:
            nodes.update(chain)
        # Every node a page asks through, whether or not the tree reaches it that
        # way: a /Group above a sheet changes how that sheet looks just the same,
        # and a node the walk never saw is still a node.  The root is exempt — its
        # /Resources is one dictionary the whole document shares, and no reorder
        # inside one document moves a page out from under it.
        for xref in sorted(nodes - {root}):
            try:
                keys: Sequence[str] = list(doc.xref_get_keys(xref))
            except Exception as exc:    # noqa: BLE001 - reported to the user
                raise EditError(
                    f"The page tree cannot be read at object {xref}: {exc}") from exc
            held = [key for key in keys
                    if key not in TREE_KEYS and key not in INHERITABLE_KEYS]
            if held:
                raise EditError(
                    f"/{held[0]} on the page tree at object {xref} is not a key a "
                    f"page can hold for itself; this document’s tree is left as it is")
            if _stated(doc, xref, "Resources") is not None:
                raise EditError(
                    f"/Resources on the page tree at object {xref} is inherited "
                    f"dictionary by dictionary, which a page cannot be given a copy "
                    f"of; this document’s tree is left as it is")
        # Whether a key is stated *anywhere* is asked of the whole tree, not of the
        # chains: a move re-parents by /Parent, so a sheet that walks past a node
        # today can be under it tomorrow — measured on a book whose /Kids and /Parent
        # disagree, where the node every page bypasses states the turn that would
        # otherwise be skipped here and adopted on the first reorder.
        live = nodes | set(reached)
        # Each node is asked once per key rather than once per page: on a tree whose
        # nodes all leave a key alone the pages are never read at all, and that is
        # what a flat document does with /CropBox and /Rotate.
        stated = {key: [value for value in (_stated(doc, node, key) for node in live)
                        if value is not None] for key in PUSHABLE_KEYS}
        for page, ancestors in chains:
            for key in PUSHABLE_KEYS:
                # A key no node states cannot start meaning something else when the
                # page moves: what it resolves to today is the specification's
                # default, and a default is the same under every ancestor.
                if not stated[key] or _stated(doc, page, key) is not None:
                    continue
                inherited = next((value for above in ancestors
                                  if (value := _stated(doc, above, key)) is not None),
                                 None)
                if inherited is None:
                    inherited = _resolves_to(doc, page, ancestors, key)
                if inherited is not None:
                    doc.xref_set_key(page, key, inherited)


def _resolves_to(doc: fitz.Document, page: int, ancestors: Tuple[int, ...],
                 key: str) -> Optional[str]:
    """What *page* answers with for *key* today, when its chain states none.

    ``/Rotate`` defaults to 0 and ``/CropBox`` to the page's ``/MediaBox`` — both
    written down in ISO 32000, and both worth pinning, because a page that stated
    nothing and resolved to 0 turns the moment it lands under a node that says 90.
    ``/MediaBox`` has no default to fall back on: a page whose whole chain omits it
    is a page MuPDF draws at a size of its own invention, and copying an invention
    onto the sheet would be a claim about the document that is not in it.
    """
    if key == "Rotate":
        return "0"
    if key == "CropBox":
        return next((value for holder in (page,) + ancestors
                     if (value := _stated(doc, holder, "MediaBox")) is not None), None)
    return None


def _walk_from_front(want: Sequence[int]) -> Tuple[Tuple[int, int], ...]:
    """The calls that lay a book out slot by slot, from the first one.

    Each call fills the slot the walk has reached, so everything below it is left
    where it is and never has to be placed again: ``move_page(frm, index)`` takes
    the sheet out of a slot above and puts it in front of the one at *index*, which
    is exactly where the fill belongs — and *frm* > *index* is not an assumption but
    a consequence, since the slots under *index* already hold the sheets they should.
    """
    calls: List[Tuple[int, int]] = []
    at = list(range(len(want)))
    for index in range(len(want)):
        frm = at.index(want[index])
        if frm == index:
            continue
        calls.append((frm, index))
        at.insert(index, at.pop(frm))
    return tuple(calls)


def _walk_from_back(want: Sequence[int]) -> Tuple[Tuple[int, int], ...]:
    """The same fill run the other way, for a book already right at the end.

    Here the sheet being placed always comes from *below* the slot, because
    everything above it has been fixed — and a sheet taken from below shifts the
    slots under it up by one, so the number handed over is *index + 1* to land on
    *index*, or ``-1`` for the last slot, which is how PyMuPDF spells "after the
    last page" (``to == page_count`` is refused outright).
    """
    calls: List[Tuple[int, int]] = []
    at = list(range(len(want)))
    last = len(want) - 1
    for index in range(last, -1, -1):
        frm = at.index(want[index])
        if frm == index:
            continue
        calls.append((frm, index + 1 if index < last else -1))
        at.insert(index, at.pop(frm))
    return tuple(calls)


def _repair_plan(want: Sequence[int]) -> Tuple[Tuple[int, int], ...]:
    """The fewer of the two walks.

    A reorder is one call per sheet it has to place, and nothing in PyMuPDF does the
    whole move at once: ``select`` builds a sub-PDF and strips the catalog, which is
    why F10-4o refused it.  The calls are cheap — 0.3 ms each measured on a settled
    540-sheet book, against the settle's own 291 — but *which* end the walk starts at
    is not a rounding error: sending the first sheet of that book to the end is one
    call from the back and 539 from the front, 0.3 ms against 61.4.  Both directions
    are simulated, and the shorter plan is run.
    """
    front = _walk_from_front(want)
    back = _walk_from_back(want)
    return back if len(back) < len(front) else front


def _reorder(doc: fitz.Document, want: Sequence[int]) -> None:
    """Settle, walk, and read the tree back — the part every in-place reorder needs.

    ``want[i]`` is the sheet slot *i* should hold, counted in the book as it is now.
    The order of the three steps is the whole content of this function:

    * The xrefs are read **before** the settle.  Not because the settle re-orders
      anything — it writes keys into the page dictionaries and moves no sheet, so
      swapping the two lines is a change with no effect, and one of the mutations run
      against this file proves it.  They are read first because they name the book the
      plan was computed against, which is the book the caller is holding.
    * The settle comes **before** the walk, because a move re-parents by ``/Parent``
      and a page whose size hangs off an ancestor gets its geometry from wherever it
      lands — measured on the nested book, where a bare ``move_page`` drew the
      260×360 sheet at 400×300 with the node it arrived in.
    * The read-back comes **after** the walk, and asks the tree what it holds by page
      object rather than by number: a reorder that got the page count right and the
      sheets wrong is the one failure a reader would notice as their document being
      quietly shuffled, and nothing in MuPDF promises ``move_page`` keeps meaning
      what it meant when this was written.
    """
    before = [doc.load_page(index).xref for index in range(doc.page_count)]
    SettlePageTree().apply(doc)
    for frm, to in _repair_plan(want):
        doc.move_page(frm, to)
    after = [doc.load_page(index).xref for index in range(doc.page_count)]
    asked = [before[sheet] for sheet in want]
    if after != asked:
        raise EditError(
            "The page tree does not read the order this reorder asked for; "
            "nothing is moved")


def _checked_pick(doc: fitz.Document, pages: Sequence[int], verb: str) -> int:
    """The book's page count, after the whole pick has been asked of it.

    Both halves are refused before a sheet moves, and both are easy to get wrong in a
    way a reader would not see coming:

    * An empty pick is nothing to do, and MuPDF would carry out a reorder of nothing
      by settling the tree and writing keys.
    * A page outside the book is not a page.  A negative index is refused here rather
      than counted from the end, because Python reads ``-1`` as the last sheet and
      ``move_page`` reads a negative *destination* as "after the last page", neither
      of which is what a pick from the thumbnail strip means.

    *verb* finishes the sentence "No page was chosen to …", which is the one thing
    the message can say that is true of the edit the reader asked for.
    """
    if not pages:
        raise EditError(f"No page was chosen to {verb}")
    count: int = doc.page_count
    word = "page" if count == 1 else "pages"
    for index in pages:
        if not 0 <= index < count:
            raise EditError(
                f"Page {index + 1} is not in this document, which has "
                f"{count} {word}")
    return count


def gap_for_drop(count: int, pages: Sequence[int], row: Optional[int],
                 after: bool) -> int:
    """The gap a thumbnail strip's drop names, in the numbering :class:`MovePages` reads.

    A reader drags a pick and lets go **between two rows**, and the rows are numbered in
    the book as shown — with the block still lying in among them.  ``MovePages`` takes a
    gap counted in the book *without* the block (``_intended`` splices it into ``rest``),
    and the two numberings differ by however many picked rows the line has already
    passed.  Everything about a drop that a reader would notice as their block landing a
    page short of where they let go lives in this one function.

    *row* is the row the pointer is over and *after* which half of it it is on: the upper
    half means the line above that row, the lower half the line below it.  ``None`` is
    blank space — below the book when *after*, above it when not — and a line drawn at
    either end names the end gap and the front gap, which is what a reader dropping onto
    the margin means.

    The answer is always a legal gap, ``0 <= gap <= count - len(set(pages))``, so the
    clamp in ``MovePages._intended`` is not reached from here.  What it is not is a
    refusal: an empty pick still names a line, and the edit that receives it is the one
    that says no page was chosen.  Nothing checks the pick twice.

    A line inside the block the reader is dragging names the gap the block already
    fills, so letting go in the middle of a contiguous pick is the order it had — and
    therefore writes nothing, settle included, which is ``MovePages``'s own
    short-circuit rather than a rule of this function.
    """
    moved = set(pages)
    if row is None:
        boundary = count if after else 0
    else:
        boundary = row + 1 if after else row
    return len([index for index in range(boundary) if index not in moved])


@dataclass(frozen=True)
class MovePages(Edit):
    """Move the picked sheets so the block starts at gap *target*.

    The rule is the reader's, not the library's: the block keeps its own order, the
    rest keep theirs, and *target* counts the gaps of the book **without** the block
    in it — which is what a drop between two rows of the thumbnail strip means, and
    is what PdfCraft's ``move_pages`` implements (``to.min(rest.len())``, so a target
    past the last gap is the end rather than an error).  A sheet named twice is one
    sheet moved once.

    Two things the edit does before anything moves:

    * **Nothing, when the block is already there.**  A drop into the gap the pick
      already fills writes no key and dirties no document — including the settle
      below, which on a book whose geometry lives on an ancestor would otherwise
      stamp a box onto every page to carry out a reorder that asks for none.
    * **The settle.**  ``SettlePageTree`` first, because a move is exactly the
      reorder a page hanging its size off an ancestor cannot survive.  The refusal
      comes with it, which is the point — a book whose ancestor holds a ``/Group`` is
      not moved, rather than moved and flattened.

    The walk and the read-back are :func:`_reorder`, shared with every reorder that
    changes a book's order without changing its sheets.

    What travels with a sheet and what does not: MuPDF re-derives the outline rows and
    the link destinations from the page objects, so those follow the sheet they named,
    and the annotations are its own.  ``/PageLabels`` follows the *position* — the
    catalog says "from index 0", not "from this sheet" — so a numbered book comes out
    of a move with new numbers printed on old sheets.  That is the reading PDFAR
    deliberately takes, and ``tests/test_page_labels.py`` holds why: a book scanned end
    to front is reversed precisely so that the numbering, which stays with the slot,
    comes out ascending over the turned sheets.
    """

    pages: Tuple[int, ...]
    target: int = 0

    scope = Scope.STRUCTURE

    @property
    def changed(self) -> Optional[Tuple[int, ...]]:
        """No page number survives the step, though no page content is touched.

        The sheets keep their words — which is why :attr:`scope` stays
        ``STRUCTURE`` and a render cache keeps its bitmap — but every one of them is
        filed under a different number, so a per-page cache cannot keep a single
        entry under the key it was written with.
        """
        return None

    def _intended(self, count: int) -> List[int]:
        """The order the tree is asked to read: ``out[i]`` is the sheet slot *i* holds."""
        moved = set(self.pages)
        block = sorted(moved)
        rest = [index for index in range(count) if index not in moved]
        at = min(max(self.target, 0), len(rest))
        return rest[:at] + block + rest[at:]

    def page_order(self, before_count: int) -> Tuple[int, ...]:
        """The same permutation, published as the map a step carries.

        Exact in both directions, because a move loses nothing: unlike the delete's
        map this one never lands a held number on a neighbour, so the reader's place,
        a bookmark, the strip's pick and the search's rows all keep pointing at their
        own sheet — or stop pointing at it, in the case of a pick that names a sheet
        the step did not move.
        """
        return tuple(self._intended(before_count))

    def changes_nothing(self, count: int) -> bool:
        """Whether this move writes the order the book is already in.

        A reader who lets go inside the block they are dragging has asked for the book
        they have.  ``apply`` already knows that — it returns before the settle — but
        the strip needs to know it too, because an ``Edit`` that writes nothing still
        takes a place on the undo stack, and a Ctrl+Z that appears to do nothing is a
        worse answer than not recording the drop at all.  The question is the edit's
        rather than the widget's so that the two of them cannot disagree.
        """
        return self._intended(count) == list(range(count))

    @property
    def label(self) -> str:
        moved = len(set(self.pages))
        if moved == 1:
            return "Move Page"
        return f"Move {moved} Pages"

    def apply(self, doc: fitz.Document) -> None:
        count = _checked_pick(doc, self.pages, "move")
        if self.target < 0:
            raise EditError(
                f"A move names the gap its block lands in, between 0 and "
                f"{count - len(set(self.pages))}, and {self.target} is not one")
        if self.changes_nothing(count):
            return
        _reorder(doc, self._intended(count))


@dataclass(frozen=True)
class ReversePages(Edit):
    """Reverse the picked sheets among the slots they already fill.

    The reader's ask, again rather than the library's: the pick's sheets come out in
    the opposite order **in the same slots**, and every sheet outside the pick stays
    exactly where it was — which is what a batch scanned back to front needs, and is
    PdfCraft's ``reverse_pages`` (``crates/organize/src/lib.rs:532``).  A pick does not
    have to be contiguous, and the middle sheet of an odd pick does not move.

    One place the two editors part company: fewer than two distinct slots.  PdfCraft
    refuses it, because its ``rebuild`` would rewrite the tree to put the book back as
    it found it.  Here it writes nothing, for the same reason a drop into the gap the
    block already fills writes nothing — a strip can hand this edit a one-thumbnail
    pick, and "nothing would change" is a worse answer than silence.  What silence
    costs is why the case is tested on a book whose geometry lives on an ancestor: a
    reverse that settled first would stamp a ``/MediaBox`` onto every page of it to
    carry out a reorder that asks for none.

    :meth:`changed` is the pick, where the move's is ``None``.  A move re-numbers
    every sheet above the gap it fills, so no entry a per-page cache holds under a
    page number survives it; a reverse leaves an unpicked sheet wearing the number it
    had, so only the slots the pick named have to be looked at again.  The rest of the
    carry is the move's, and shared with it: the settle first, ``_repair_plan``'s
    shorter walk, and the read-back that refuses a tree which does not come out in the
    order asked for.

    What travels with a sheet travels with it here too — outline rows, link
    destinations, annotations — and ``/PageLabels`` still numbers positions, which is
    the reading ``tests/test_page_labels.py`` pins: this is the edit a back-to-front
    scan exists for, and a numbering that followed the sheets would undo it.
    """

    pages: Tuple[int, ...]

    scope = Scope.STRUCTURE

    def _intended(self, count: int) -> List[int]:
        """The order the tree is asked to read: ``out[i]`` is the sheet slot *i* holds."""
        slots = sorted(set(self.pages))
        order = list(range(count))
        for step, slot in enumerate(slots):
            order[slot] = slots[-1 - step]
        return order

    def page_order(self, before_count: int) -> Tuple[int, ...]:
        """The same permutation, published as the map a step carries.

        Exact in both directions, and its own inverse: the walk is a swap of pairs, so
        the map that runs the reader's page onto its new sheet is the one that runs it
        home, and the unpicked sheets answer with the number they already had.
        """
        return tuple(self._intended(before_count))

    def changes_nothing(self, count: int) -> bool:
        """Whether this reverse writes the order the book is already in.

        True for a pick of one sheet — a strip can offer *Reverse* on the thumbnail under
        the cursor — and for no other pick, since two slots holding different sheets
        always change hands.  Asked by the surface before it records anything, for the
        same reason ``apply`` returns early: a step that writes nothing still takes a
        place on the undo stack.
        """
        return self._intended(count) == list(range(count))

    @property
    def label(self) -> str:
        turned = len(set(self.pages))
        if turned == 1:
            return "Reverse Page"
        return f"Reverse {turned} Pages"

    def apply(self, doc: fitz.Document) -> None:
        count = _checked_pick(doc, self.pages, "reverse")
        if self.changes_nothing(count):
            return
        _reorder(doc, self._intended(count))
