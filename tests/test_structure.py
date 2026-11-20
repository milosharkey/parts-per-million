"""The benzene-ring check in structure.py.

The fixed cases always run. The comparison with RDKit (the standard
chemistry toolkit) runs only if RDKit happens to be installed: it isn't a
requirement of the project (DECISIONS.md, 34).
"""
import pytest

from structure import has_benzene_ring

CONTAINS_BENZENE = {
    "benzene, aromatic form": "c1ccccc1",
    "benzene, Kekulé form (how Wikidata usually writes it)": "C1=CC=CC=C1",
    "toluene": "CC1=CC=CC=C1",
    "aspirin": "CC(=O)OC1=CC=CC=C1C(=O)O",
    "TNT, with charges in brackets": "CC1=C(C=C(C=C1[N+](=O)[O-])[N+](=O)[O-])[N+](=O)[O-]",
    "naphthalene, fused rings": "C1=CC=C2C=CC=CC2=C1",
    "serotonin, indole's benzene ring": "C1=CC2=C(C=C1O)C(=CN2)CCN",
    "oestradiol, one aromatic ring in a steroid": "C[C@]12CC[C@H]3[C@H]([C@@H]1CC[C@@H]2O)CCC4=C3C=CC(=C4)O",
    "biphenyl with an explicit single bond": "c1ccc(cc1)-c1ccccc1",
    "sodium benzoate, a salt": "[Na+].[O-]C(=O)C1=CC=CC=C1",
    "isotope label": "[13cH]1ccccc1",
    "ring closure numbers over 9": "C%10=CC=CC=C%10",
}
NO_BENZENE = {
    "cyclohexane": "C1CCCCC1",
    "cyclohexene": "C1=CCCCC1",
    "1,3-cyclohexadiene": "C1=CC=CCC1",
    "p-benzoquinone, double bonds point outwards": "C1=CC(=O)C=CC1=O",
    "pyridine, a nitrogen in the ring": "C1=CC=NC=C1",
    "thiophene, five-membered": "C1=CSC=C1",
    "azulene, 5 and 7 rings": "c1ccc2cccc2cc1",
    "caffeine": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
    "cholesterol": "CC(C)CCCC(C)C1CCC2C1(CCC3C2CC=C4C3(CCC(C4)O)C)C",
    "glucose": "C(C1C(C(C(C(O1)O)O)O)O)O",
    "empty string": "",
    "not SMILES at all": "this is not SMILES",
    "unclosed ring": "C1=CC=CC=C",
}


@pytest.mark.parametrize("name", CONTAINS_BENZENE)
def test_finds_benzene_rings(name):
    assert has_benzene_ring(CONTAINS_BENZENE[name])


@pytest.mark.parametrize("name", NO_BENZENE)
def test_rejects_everything_else(name):
    assert not has_benzene_ring(NO_BENZENE[name])


def test_agrees_with_rdkit_on_random_spellings():
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")
    pattern = Chem.MolFromSmarts("c1ccccc1")
    for smiles in [*CONTAINS_BENZENE.values(), *NO_BENZENE.values()]:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None or not smiles:
            continue
        expected = mol.HasSubstructMatch(pattern)
        kekule = Chem.Mol(mol)
        Chem.Kekulize(kekule, clearAromaticFlags=True)
        for _ in range(25):
            for variant in (Chem.MolToSmiles(mol, doRandom=True),
                            Chem.MolToSmiles(kekule, doRandom=True, kekuleSmiles=True)):
                assert has_benzene_ring(variant) == expected, variant
