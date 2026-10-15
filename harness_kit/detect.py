"""Detect the stack, standard commands and current harness state of an existing project.

Only reads files; never runs any project command. The result is a plain dict
shared by audit.py and scaffold.py.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
    ".next", ".nuxt", "coverage", "target", "bin", "obj", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "vendor", ".gradle",
    ".harness", ".claude",
}

SOURCE_DIR_NAMES = ["src", "lib", "app", "pkg", "cmd", "internal", "server", "client", "packages", "apps", "services"]
TEST_DIR_NAMES = ["tests", "test", "__tests__", "spec", "e2e", "testing"]
ENTRY_FILES = ["AGENTS.md", "CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md", "GEMINI.md"]
PROGRESS_FILES = ["PROGRESS.md", "claude-progress.md", "claude-progress.txt", "progress.md"]
FEATURE_FILES = ["feature_list.json", "features.json", "docs/features.md"]
DECISION_FILES = ["docs/DECISIONS.md", "DECISIONS.md", "docs/adr", "docs/decisions", "adr"]
ARCH_FILES = ["docs/ARCHITECTURE.md", "ARCHITECTURE.md", "docs/architecture.md"]
CI_FILES = [".github/workflows", ".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".circleci", "bitbucket-pipelines.yml"]
CONTAINER_FILES = ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", ".devcontainer"]
RUNTIME_PINS = [".python-version", ".nvmrc", ".node-version", ".tool-versions", "rust-toolchain.toml",
                "rust-toolchain", "global.json", ".java-version", ".ruby-version", ".sdkmanrc", "mise.toml"]
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".java", ".kt",
             ".go", ".rs", ".cs", ".php", ".rb", ".swift", ".scala", ".c", ".cc", ".cpp", ".h", ".hpp", ".sql"}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_toml(path: Path) -> dict:
    if tomllib is None:
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _step(cmd: str, cwd: str = ".") -> dict:
    return {"cmd": cmd, "cwd": cwd}


# ---------------------------------------------------------------- stacks ---

def _detect_node(d: Path, rel: str) -> dict | None:
    pkg_path = d / "package.json"
    if not pkg_path.exists():
        return None
    pkg = _read_json(pkg_path)
    scripts = pkg.get("scripts", {}) or {}
    if (d / "pnpm-lock.yaml").exists():
        pm, install = "pnpm", "pnpm install --frozen-lockfile"
    elif (d / "yarn.lock").exists():
        pm, install = "yarn", "yarn install --frozen-lockfile"
    elif (d / "bun.lockb").exists() or (d / "bun.lock").exists():
        pm, install = "bun", "bun install"
    elif (d / "package-lock.json").exists():
        pm, install = "npm", "npm ci"
    else:
        pm, install = "npm", "npm install"

    def run(name: str) -> str:
        return f"{pm} test" if name == "test" and pm == "npm" else f"{pm} run {name}"

    def first(*names: str) -> str | None:
        for n in names:
            if n in scripts:
                return run(n)
        return None

    test = first("test", "test:unit", "unit")
    if test and "no test specified" in str(scripts.get("test", "")):
        test = None
    typecheck = first("typecheck", "type-check", "check-types", "tsc", "types")
    if not typecheck and (d / "tsconfig.json").exists():
        typecheck = "npx tsc --noEmit"
    e2e = first("test:e2e", "e2e", "cy:run", "playwright")
    if not e2e and any((d / f).exists() for f in ("playwright.config.ts", "playwright.config.js")):
        e2e = "npx playwright test"
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    frameworks = [f for f in ("react", "next", "vue", "nuxt", "svelte", "@angular/core", "express", "fastify",
                              "@nestjs/core", "electron", "vite", "jest", "vitest", "typescript") if f in deps]
    return {
        "name": "node", "dir": rel, "package_manager": pm,
        "language": "TypeScript" if "typescript" in deps or (d / "tsconfig.json").exists() else "JavaScript",
        "version_hint": (pkg.get("engines") or {}).get("node", ""),
        "frameworks": frameworks,
        "lockfile": pm != "npm" or (d / "package-lock.json").exists(),
        "commands": {
            "setup": install, "dev": first("dev", "start", "serve"), "test": test,
            "lint": first("lint", "eslint"), "typecheck": typecheck, "build": first("build"), "e2e": e2e,
        },
    }


def _detect_python(d: Path, rel: str) -> dict | None:
    markers = ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile", "manage.py"]
    if not any((d / m).exists() for m in markers):
        return None
    pyproject = _read_toml(d / "pyproject.toml")
    tool = pyproject.get("tool", {}) if pyproject else {}
    if (d / "uv.lock").exists():
        pm, install, prefix = "uv", "uv sync", "uv run "
    elif (d / "poetry.lock").exists() or "poetry" in tool:
        pm, install, prefix = "poetry", "poetry install", "poetry run "
    elif (d / "Pipfile").exists():
        pm, install, prefix = "pipenv", "pipenv install --dev", "pipenv run "
    else:
        pm, prefix = "pip", "python -m "
        if (d / "requirements-dev.txt").exists():
            install = "python -m pip install -r requirements-dev.txt"
        elif (d / "requirements.txt").exists():
            install = "python -m pip install -r requirements.txt"
        else:
            install = "python -m pip install -e ."
    setup_cfg = _read_text(d / "setup.cfg")
    has_tests = any((d / t).is_dir() for t in ("tests", "test")) or "pytest" in tool or (d / "pytest.ini").exists()
    if (d / "manage.py").exists() and not ("pytest" in tool or (d / "pytest.ini").exists()):
        test = f"python manage.py test"
    elif has_tests:
        test = f"{prefix}pytest -x -q"
    else:
        test = None
    if "ruff" in tool or (d / "ruff.toml").exists() or (d / ".ruff.toml").exists():
        lint = f"{prefix}ruff check ."
    elif (d / ".flake8").exists() or "[flake8]" in setup_cfg:
        lint = f"{prefix}flake8"
    elif "pylint" in tool or (d / ".pylintrc").exists():
        lint = f"{prefix}pylint {rel if rel != '.' else '.'}"
    else:
        lint = None
    if "mypy" in tool or (d / "mypy.ini").exists() or "[mypy]" in setup_cfg:
        typecheck = f"{prefix}mypy ."
    elif "pyright" in tool or (d / "pyrightconfig.json").exists():
        typecheck = "npx pyright" if pm == "pip" else f"{prefix}pyright"
    else:
        typecheck = None
    reqs = _read_text(d / "requirements.txt").lower() + json.dumps(pyproject).lower()
    frameworks = [f for f in ("django", "flask", "fastapi", "sqlalchemy", "celery", "pandas", "pytest", "pydantic")
                  if f in reqs]
    dev = None
    if (d / "manage.py").exists():
        dev = "python manage.py runserver"
    elif "fastapi" in frameworks:
        # no inline "# comment": cmd.exe would pass it to uvicorn as arguments. `main:app` is a guess the
        # retrofit skill confirms (H001) by editing commands.dev in .harness/config.json.
        dev = f"{prefix}uvicorn main:app --reload"
    return {
        "name": "python", "dir": rel, "package_manager": pm, "language": "Python",
        "version_hint": (pyproject.get("project", {}) or {}).get("requires-python", "") if pyproject else "",
        "frameworks": frameworks,
        "lockfile": any((d / f).exists() for f in ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt")),
        "commands": {"setup": install, "dev": dev, "test": test, "lint": lint, "typecheck": typecheck,
                     "build": None, "e2e": None},
    }


def _detect_java(d: Path, rel: str) -> dict | None:
    if (d / "pom.xml").exists():
        mvn = "./mvnw" if (d / "mvnw").exists() else "mvn"
        pom = _read_text(d / "pom.xml")
        return {"name": "java-maven", "dir": rel, "package_manager": "maven", "language": "Java",
                "version_hint": "", "frameworks": ["spring-boot"] if "spring-boot" in pom else [],
                "lockfile": True, "_members": _maven_modules(pom),
                "commands": {"setup": f"{mvn} -B -q dependency:resolve", "dev": None, "test": f"{mvn} -B -q test",
                             "lint": None, "typecheck": f"{mvn} -B -q compile", "build": f"{mvn} -B -q -DskipTests package",
                             "e2e": f"{mvn} -B -q verify"}}
    if any((d / f).exists() for f in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
        gw = "./gradlew" if (d / "gradlew").exists() else "gradle"
        text = _read_text(d / "build.gradle") + _read_text(d / "build.gradle.kts")
        return {"name": "java-gradle", "dir": rel, "package_manager": "gradle",
                # a .kts build script is not a Kotlin project; the Kotlin plugin is
                "language": "Kotlin" if re.search(r"kotlin\(|org\.jetbrains\.kotlin", text) else "Java",
                "version_hint": "",
                "frameworks": ["spring-boot"] if "org.springframework.boot" in text else [], "lockfile": True,
                "_members": _gradle_includes(_read_text(d / "settings.gradle") + _read_text(d / "settings.gradle.kts")),
                "commands": {"setup": f"{gw} dependencies -q", "dev": None, "test": f"{gw} test",
                             "lint": f"{gw} check -x test", "typecheck": f"{gw} classes",
                             "build": f"{gw} build -x test", "e2e": None}}
    return None


def _detect_go(d: Path, rel: str) -> dict | None:
    if not (d / "go.mod").exists():
        return None
    m = re.search(r"^go\s+(\S+)", _read_text(d / "go.mod"), re.M)
    return {"name": "go", "dir": rel, "package_manager": "go", "language": "Go",
            "version_hint": m.group(1) if m else "", "frameworks": [], "lockfile": (d / "go.sum").exists(),
            "commands": {"setup": "go mod download", "dev": None, "test": "go test ./...", "lint": "go vet ./...",
                         "typecheck": "go build ./...", "build": "go build ./...", "e2e": None}}


def _detect_rust(d: Path, rel: str) -> dict | None:
    if not (d / "Cargo.toml").exists():
        return None
    members = _cargo_members(_read_text(d / "Cargo.toml"))
    # a root that also has [package] only tests itself unless told otherwise: members must stay under the gate
    ws = " --workspace" if any(not m.startswith("!") for m in members) else ""
    return {"name": "rust", "dir": rel, "package_manager": "cargo", "language": "Rust", "version_hint": "",
            "frameworks": [], "lockfile": (d / "Cargo.lock").exists(), "_members": members,
            "commands": {"setup": "cargo fetch", "dev": None, "test": f"cargo test{ws}",
                         "lint": f"cargo clippy{ws} -- -D warnings", "typecheck": f"cargo check{ws}",
                         "build": f"cargo build{ws}", "e2e": None}}


DOTNET_LANGS = {".csproj": "C#", ".fsproj": "F#", ".vbproj": "VB.NET"}


def _dotnet_projects(d: Path, depth: int = 3) -> list[Path]:
    """Project files at most `depth` levels down, never inside SKIP_DIRS or dot-dirs (avoids walking
    node_modules/bin/obj). Trade-off: deeper projects, or ones under e.g. `build/`, are not seen."""
    found, frontier = [], [d]
    for _ in range(depth + 1):
        nxt = []
        for folder in frontier:
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            found += [e for e in entries if e.is_file() and e.suffix.lower() in DOTNET_LANGS]
            nxt += [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".")]
        frontier = nxt
    return found


def _detect_dotnet(d: Path, rel: str) -> dict | None:
    here = [p for p in d.iterdir()
            if p.is_file() and (p.suffix.lower() in DOTNET_LANGS or p.suffix.lower() in (".sln", ".slnx"))]
    if not here:
        return None
    projects = _dotnet_projects(d)
    langs = [DOTNET_LANGS[p.suffix.lower()] for p in projects]
    # deterministic: most project files wins, ties broken by DOTNET_LANGS order (C#, F#, VB.NET)
    language = max(DOTNET_LANGS.values(), key=langs.count) if langs else "C#"
    return {"name": "dotnet", "dir": rel, "package_manager": "dotnet", "language": language,
            "version_hint": "", "frameworks": [],
            "lockfile": any((p.parent / "packages.lock.json").exists() for p in projects),
            "_members": _solution_project_dirs([p for p in here if p.suffix.lower() in (".sln", ".slnx")]),
            "commands": {"setup": "dotnet restore", "dev": None, "test": "dotnet test --no-restore",
                         "lint": "dotnet format --verify-no-changes", "typecheck": "dotnet build --no-restore",
                         "build": "dotnet build --no-restore -c Release", "e2e": None}}


def _detect_php(d: Path, rel: str) -> dict | None:
    if not (d / "composer.json").exists():
        return None
    scripts = (_read_json(d / "composer.json").get("scripts") or {})
    test = "composer test" if "test" in scripts else (
        # `php vendor/bin/...`: cmd.exe would parse a bare `vendor/bin/x` as `vendor` + the switch `/bin`
        "php vendor/bin/phpunit" if any((d / f).exists() for f in ("phpunit.xml", "phpunit.xml.dist")) else None)
    return {"name": "php", "dir": rel, "package_manager": "composer", "language": "PHP", "version_hint": "",
            "frameworks": ["laravel"] if (d / "artisan").exists() else [], "lockfile": (d / "composer.lock").exists(),
            "commands": {"setup": "composer install", "dev": "php artisan serve" if (d / "artisan").exists() else None,
                         "test": test, "lint": "composer lint" if "lint" in scripts else None,
                         "typecheck": "php vendor/bin/phpstan analyse"
                         if any((d / f).exists() for f in ("phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon"))
                         else None,
                         "build": None, "e2e": None}}


def _detect_ruby(d: Path, rel: str) -> dict | None:
    if not (d / "Gemfile").exists():
        return None
    # a bare Gemfile (cocoapods/fastlane in mobile repos) has no test suite: emit no test command rather than a failing one
    if (d / "spec").is_dir():
        test = "bundle exec rspec"
    elif (d / "Rakefile").exists() or (d / "test").is_dir():
        test = "bundle exec rake test"
    else:
        test = None
    return {"name": "ruby", "dir": rel, "package_manager": "bundler", "language": "Ruby", "version_hint": "",
            "frameworks": ["rails"] if (d / "bin" / "rails").exists() else [], "lockfile": (d / "Gemfile.lock").exists(),
            "commands": {"setup": "bundle install", "dev": "ruby bin/rails server" if (d / "bin" / "rails").exists() else None,
                         "test": test,
                         "lint": "bundle exec rubocop" if (d / ".rubocop.yml").exists() else None,
                         "typecheck": None, "build": None, "e2e": None}}


DETECTORS = [_detect_node, _detect_python, _detect_java, _detect_go, _detect_rust, _detect_dotnet,
             _detect_php, _detect_ruby]


def detect_stacks(root: Path) -> list[dict]:
    """Find stacks in the root and in subdirectories up to 2 levels deep (monorepo)."""
    stacks: list[dict] = []
    candidates = [root]
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name not in SKIP_DIRS and not child.name.startswith("."):
            candidates.append(child)
            for grand in sorted(child.iterdir()) if child.name in ("packages", "apps", "services") else []:
                if grand.is_dir() and grand.name not in SKIP_DIRS and not grand.name.startswith("."):
                    candidates.append(grand)
    for d in candidates:
        rel = d.relative_to(root).as_posix() if d != root else "."
        for det in DETECTORS:
            try:
                found = det(d, rel)
            except OSError:
                found = None
            if found:
                stacks.append(found)
    return _drop_submodules(stacks)


# --------------------------------------------------- multi-module roots ---
# Each returns member directories relative to the build root (cargo entries may be globs; "!x" excludes).

def _maven_modules(pom: str) -> list[str]:
    pom = re.sub(r"<!--.*?-->", "", pom, flags=re.S)
    pom = re.sub(r"<profiles>.*?</profiles>", "", pom, flags=re.S)  # profile-only modules are not built by default
    mods = re.findall(r"<module>\s*([^<]+?)\s*</module>", pom)
    return [re.sub(r"/pom\.xml$", "", m.strip().lstrip("./").rstrip("/")) for m in mods]


def _gradle_includes(settings: str) -> list[str]:
    settings = re.sub(r"//[^\n]*|/\*.*?\*/", "", settings, flags=re.S)
    # Kotlin/Groovy call with parentheses (may span lines), or Groovy command syntax with `,` line continuations
    groups = re.findall(r"\binclude\s*\(([^)]*)\)", settings, re.S)
    groups += re.findall(r"^\s*include\s+((?:[^\n(]*,[ \t]*\n)*[^\n(]*)$", settings, re.M)
    out = []
    for g in groups:
        out += [p.strip(":").replace(":", "/") for p in re.findall(r"""["']([^"']+)["']""", g)]
    return out


def _cargo_members(cargo: str) -> list[str]:
    m = re.search(r"^\[workspace\](.*?)(?=^\[|\Z)", cargo, re.M | re.S)
    if not m:
        return []
    def arr(key: str) -> list[str]:
        a = re.search(r"(?<![\w-])" + key + r"\s*=\s*\[(.*?)\]", m.group(1), re.S)  # not default-members
        return re.findall(r"""["']([^"']+)["']""", a.group(1)) if a else []
    return [x.rstrip("/") for x in arr("members")] + ["!" + x.rstrip("/") for x in arr("exclude")]


def _solution_project_dirs(solutions: list[Path]) -> list[str]:
    out = []
    for sln in solutions:
        text = _read_text(sln)
        paths = re.findall(r'"([^"]+\.(?:cs|fs|vb)proj)"', text) + re.findall(r'Path="([^"]+\.(?:cs|fs|vb)proj)"', text)
        out += [p.replace("\\", "/").rsplit("/", 1)[0] if "/" in p.replace("\\", "/") else "." for p in paths]
    return out


def _is_member(members: list[str], rel: str) -> bool:
    if any(fnmatch.fnmatch(rel, m[1:]) for m in members if m.startswith("!")):
        return False
    return any(fnmatch.fnmatch(rel, m) for m in members if not m.startswith("!"))


# build systems whose root builds the modules it lists (pom <modules>, settings include, .sln, cargo workspace)
MULTI_MODULE = {"java-maven", "java-gradle", "dotnet", "rust"}


def _drop_submodules(stacks: list[dict]) -> list[dict]:
    """Drop a stack that is a declared member of an ancestor root of the same build system (it is built by that
    root and its wrapper); fold its language/frameworks into the root. Undeclared directories, siblings and plain
    single-project roots are kept. The internal `_members` key is removed from every stack."""
    def rel_to(parent: str, child: str) -> str | None:
        if parent == child:
            return None
        if parent == ".":
            return child
        return child[len(parent) + 1:] if child.startswith(parent + "/") else None

    owners: dict[int, dict] = {}
    for s in stacks:
        if s["name"] in MULTI_MODULE:
            for o in stacks:
                rel = rel_to(o["dir"], s["dir"]) if o["name"] == s["name"] else None
                if rel and _is_member(o.get("_members") or [], rel):
                    owners[id(s)] = o
                    break
    # fold deepest first, so a leaf's frameworks travel up a nested chain (root -> services -> services/a)
    for s in sorted(stacks, key=lambda x: -x["dir"].count("/") - (x["dir"] != ".")):
        owner = owners.get(id(s))
        if owner is not None:
            owner["frameworks"] = sorted(set(owner["frameworks"]) | set(s["frameworks"]))
            if owner["language"] == "Java" and s["language"] == "Kotlin":
                owner["language"] = "Kotlin"
    kept = [s for s in stacks if id(s) not in owners]
    for s in stacks:
        s.pop("_members", None)
    return kept


def merge_commands(stacks: list[dict]) -> dict[str, list[dict]]:
    """Merge stack commands into {command name: [steps]} for .harness/config.json."""
    merged: dict[str, list[dict]] = {k: [] for k in ("setup", "dev", "test", "lint", "typecheck", "build", "e2e")}
    for s in stacks:
        for key, cmd in s["commands"].items():
            if cmd:
                merged[key].append(_step(cmd, s["dir"]))
    return merged


# ------------------------------------------------------------- inventory ---

def walk_files(root: Path, max_files: int = 20000):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            yield Path(dirpath) / f
            count += 1
            if count >= max_files:
                return


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def churn(root: Path, since: str = "12 months ago") -> dict[str, int]:
    """Number of commits touching each file within the period (used for hotspots)."""
    log = git(root, "log", f"--since={since}", "--name-only", "--pretty=format:")
    counts: dict[str, int] = {}
    for line in log.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts


def exists_any(root: Path, names: list[str]) -> list[str]:
    return [n for n in names if (root / n).exists()]


def inventory(root: Path) -> dict:
    root = root.resolve()
    stacks = detect_stacks(root)
    code_files: list[tuple[str, int]] = []
    test_files = 0
    for p in walk_files(root):
        if p.suffix.lower() in CODE_EXTS:
            rel = p.relative_to(root).as_posix()
            if rel == "scripts/harness.py":  # harness-managed file, not project code
                continue
            lines = count_lines(p)
            code_files.append((rel, lines))
            low = rel.lower()
            if re.search(r"(^|/)(tests?|__tests__|spec|e2e)/", low) or re.search(
                    r"(^test_|_test\.|\.test\.|\.spec\.|tests?\.)", p.name.lower()):
                test_files += 1
    total_lines = sum(n for _, n in code_files)
    is_git = (root / ".git").exists()
    ch = churn(root) if is_git else {}
    hotspots = sorted(
        ({"file": f, "lines": n, "commits": ch.get(f, 0), "score": n * (1 + ch.get(f, 0))}
         for f, n in code_files if n >= 150),
        key=lambda h: h["score"], reverse=True)[:15]
    source_dirs = [d for d in SOURCE_DIR_NAMES if (root / d).is_dir()]
    # Python / Go packages at the root (e.g. shop/__init__.py) are source dirs too
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name not in SKIP_DIRS and child.name not in source_dirs \
                and child.name not in TEST_DIR_NAMES and not child.name.startswith(".") \
                and ((child / "__init__.py").exists() or any(child.glob("*.go"))):
            source_dirs.append(child.name)
    modules: list[str] = []
    for sd in source_dirs or ["."]:
        base = root / sd
        for child in sorted(base.iterdir()) if base.is_dir() else []:
            if child.is_dir() and child.name not in SKIP_DIRS and child.name not in TEST_DIR_NAMES \
                    and child.name not in ("docs", "scripts") and not child.name.startswith((".", "_")):
                if any(f.suffix in CODE_EXTS for f in child.rglob("*") if f.is_file()):
                    modules.append(child.relative_to(root).as_posix())
    modules = modules[:30]
    return {
        "root": str(root),
        "name": root.name,
        "stacks": stacks,
        "commands": merge_commands(stacks),
        "is_git": is_git,
        "last_commit": git(root, "log", "-1", "--pretty=%h %s").strip() if is_git else "",
        "commit_count": int(git(root, "rev-list", "--count", "HEAD").strip() or 0) if is_git else 0,
        "code_files": len(code_files),
        "code_lines": total_lines,
        "test_files": test_files,
        "largest_files": sorted(({"file": f, "lines": n} for f, n in code_files), key=lambda x: -x["lines"])[:10],
        "hotspots": hotspots,
        "source_dirs": source_dirs,
        "test_dirs": [d for d in TEST_DIR_NAMES if (root / d).is_dir()],
        "modules": modules,
        "entry_files": exists_any(root, ENTRY_FILES),
        "progress_files": exists_any(root, PROGRESS_FILES),
        "feature_files": exists_any(root, FEATURE_FILES),
        "decision_files": exists_any(root, DECISION_FILES),
        "architecture_files": exists_any(root, ARCH_FILES),
        "ci": exists_any(root, CI_FILES),
        "containers": exists_any(root, CONTAINER_FILES),
        "runtime_pins": exists_any(root, RUNTIME_PINS),
        "has_readme": bool(exists_any(root, ["README.md", "README.rst", "README.txt", "README"])),
        "has_gitignore": (root / ".gitignore").exists(),
        "has_env_example": bool(exists_any(root, [".env.example", ".env.sample", ".env.template"])),
        "init_scripts": exists_any(root, ["init.sh", "init.ps1", "Makefile", "justfile", "Taskfile.yml",
                                          "scripts/setup.sh", "bin/setup"]),
        "harness_config": (root / ".harness" / "config.json").exists(),
    }
