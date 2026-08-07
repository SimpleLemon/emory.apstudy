import ast
import unittest
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    REPO_ROOT,
    REPO_ROOT / "blueprints",
    REPO_ROOT / "services",
    REPO_ROOT / "scripts",
)
EXPECTED_OS_ENVIRON_ACCESS = Counter(
    {
        ("app.py", "setdefault"): 1,
        ("blueprints/admin.py", "mapping"): 2,
        ("config.py", "get"): 1,
        ("scripts/backup_nest_db.py", "get"): 2,
        ("services/host_admin.py", "mapping"): 2,
    }
)


def _source_files():
    seen = set()
    for source_root in SOURCE_ROOTS:
        candidates = source_root.glob("*.py") if source_root == REPO_ROOT else source_root.rglob("*.py")
        for path in candidates:
            if path not in seen:
                seen.add(path)
                yield path


def _environment_accesses(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    accesses = []
    imported_aliases = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            imported_aliases.extend(
                alias.name for alias in node.names if alias.name in {"environ", "getenv"}
            )
        if not (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            continue
        parent = parents.get(node)
        access = (
            parent.attr
            if isinstance(parent, ast.Attribute) and parent.value is node
            else "mapping"
        )
        accesses.append(access)
    return accesses, imported_aliases


class EnvironmentAccessBoundaryTests(unittest.TestCase):
    def test_direct_environment_access_is_limited_to_documented_boundaries(self):
        actual = Counter()
        imported_aliases = []
        for path in _source_files():
            relative_path = path.relative_to(REPO_ROOT).as_posix()
            accesses, aliases = _environment_accesses(path)
            actual.update((relative_path, access) for access in accesses)
            imported_aliases.extend((relative_path, alias) for alias in aliases)

        self.assertEqual(actual, EXPECTED_OS_ENVIRON_ACCESS)
        self.assertEqual(imported_aliases, [])


if __name__ == "__main__":
    unittest.main()
