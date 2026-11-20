"""Reading molecular structures (SMILES strings) to check what a compound contains.

Used by prompts that are defined by structure rather than by a list, such
as "Name a compound with a benzene ring". Wikidata stores a SMILES string
for most compounds (canonical SMILES, P233, or isomeric SMILES, P2017):
a line of text that spells out the atoms, the bonds between them and where
rings close. benzene is "c1ccccc1" in aromatic form or "C1=CC=CC=C1" in
Kekulé form (alternating double bonds), and Wikidata mostly uses the second.

Why not RDKit, the standard chemistry toolkit: it's a large compiled
package whose builds lag behind new Python versions, and this pipeline
needs exactly one question answered. The parser below is about 100 lines,
has no dependencies, and is checked against RDKit in the tests
(tests/test_structure.py) on thousands of random spellings of real
molecules. See DECISIONS.md, 34.

Only what the benzene check needs is read: atoms (with their element and
whether they're written as aromatic), bonds and their orders, branches and
ring closures. Charges, isotopes, hydrogens and stereochemistry are skipped.
"""
from __future__ import annotations

import re

# Two-letter elements that can appear inside brackets ([Na+], [Se], [as]).
# Outside brackets, only the "organic subset" is allowed: B C N O P S F Cl Br I.
_TWO_LETTER = {
    "He", "Li", "Be", "Ne", "Na", "Mg", "Al", "Si", "Cl", "Ar", "Ca", "Sc", "Ti", "Cr", "Mn",
    "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Zr", "Nb",
    "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "Xe", "Cs", "Ba", "La",
    "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf",
    "Ta", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra",
    "Ac", "Th", "Pa", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf",
    "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
    "se", "as", "te",   # aromatic forms
}
_BOND_ORDER = {"-": 1, "=": 2, "#": 3, "$": 4, ":": 1.5, "/": 1, "\\": 1}
_TOKEN = re.compile(r"\[[^\]]*\]|Br|Cl|[BCNOPSFIbcnops*]|%\d\d|\d|[-=#$:/\\().]")


class SmilesError(ValueError):
    """The string isn't SMILES this reader understands."""


def _bracket_element(token: str) -> str:
    """The element inside a bracket atom: "[13CH3]" -> "C", "[nH]" -> "n"."""
    body = token[1:-1].lstrip("0123456789")   # skip the isotope number
    if body[:2] in _TWO_LETTER:
        return body[:2]
    if body[:1].isalpha() or body[:1] == "*":
        return body[:1]
    raise SmilesError(f"can't read atom {token}")


def parse(smiles: str) -> tuple[list[tuple[str, bool]], dict[int, dict[int, float]]]:
    """Atoms as (element, written aromatic?) and bonds as {atom: {neighbour: order}}.

    Bond order 1.5 means an aromatic bond: written ":" or implied between two
    atoms written in lower case.
    """
    atoms: list[tuple[str, bool]] = []
    bonds: dict[int, dict[int, float]] = {}
    stack: list[int] = []            # atoms to return to after a branch
    rings: dict[str, tuple[int, float | None]] = {}   # open ring closures
    previous: int | None = None
    pending: float | None = None     # an explicit bond waiting for its next atom

    def bond(a: int, b: int, order: float | None) -> None:
        if order is None:
            order = 1.5 if atoms[a][1] and atoms[b][1] else 1
        bonds[a][b] = bonds[b][a] = order

    pos = 0
    for match in _TOKEN.finditer(smiles):
        if match.start() != pos:
            raise SmilesError(f"unexpected {smiles[pos:match.start()]!r}")
        pos = match.end()
        t = match.group()
        if t[0] == "[" or t[0].isalpha() or t == "*":
            element = _bracket_element(t) if t[0] == "[" else t
            atoms.append((element.capitalize() if element != "*" else "*", element.islower()))
            bonds[len(atoms) - 1] = {}
            if previous is not None:
                bond(previous, len(atoms) - 1, pending)
            previous, pending = len(atoms) - 1, None
        elif t in _BOND_ORDER:
            pending = _BOND_ORDER[t]
        elif t == "(":
            stack.append(previous)
        elif t == ")":
            if not stack:
                raise SmilesError("unbalanced brackets")
            previous = stack.pop()
        elif t == ".":
            previous, pending = None, None   # separate molecule (a salt, say)
        else:                                # ring closure digit or %nn
            if previous is None:
                raise SmilesError("ring closure before any atom")
            if t in rings:
                other, order = rings.pop(t)
                bond(other, previous, pending if pending is not None else order)
            else:
                rings[t] = (previous, pending)
            pending = None
    if pos != len(smiles) or stack or rings:
        raise SmilesError("incomplete SMILES")
    return atoms, bonds


def has_benzene_ring(smiles: str) -> bool:
    """True if any six-membered ring of carbons is a benzene ring.

    A benzene ring here is six carbon atoms in a ring that are either all
    written as aromatic (c1ccccc1) or joined by alternating double and
    single bonds (C1=CC=CC=C1, the Kekulé form). Rings with any other atom
    (pyridine's nitrogen) don't count, and neither do rings with fewer
    double bonds (cyclohexene) or with double bonds pointing out of the ring
    (benzoquinone). Fused ring systems (naphthalene, indole, steroids like
    oestradiol) count as long as one ring is a benzene ring. Unreadable
    SMILES count as no.
    """
    try:
        atoms, bonds = parse(smiles.strip())
    except SmilesError:
        return False
    carbons = {i for i, (element, _) in enumerate(atoms) if element == "C"}

    def is_benzene(ring: list[int]) -> bool:
        orders = [bonds[ring[i]][ring[(i + 1) % 6]] for i in range(6)]
        if all(atoms[i][1] for i in ring) and all(o == 1.5 for o in orders):
            return True
        return orders in ([2, 1] * 3, [1, 2] * 3)

    # Search for 6-rings by walking paths of carbons, starting each ring at
    # its lowest-numbered atom so every ring is only tried a couple of times.
    def walk(path: list[int]) -> bool:
        last = path[-1]
        for nxt in bonds[last]:
            if nxt == path[0] and len(path) == 6:
                if is_benzene(path):
                    return True
            elif nxt in carbons and nxt > path[0] and nxt not in path and len(path) < 6:
                if walk(path + [nxt]):
                    return True
        return False

    return any(walk([start]) for start in sorted(carbons))
