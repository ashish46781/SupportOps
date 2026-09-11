from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from app.models import EvidenceItem
from app.stores.qdrant import content_hash


@dataclass(slots=True)
class PythonSymbol:
    kind: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    docstring: str | None
    source: str
    imports: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    endpoint: str | None = None


def parse_python_file(path: Path, repository_root: Path) -> list[PythonSymbol]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    relative = path.relative_to(repository_root).as_posix()
    module_name = relative.removesuffix(".py").replace("/", ".")
    imports = _imports(tree)
    symbols = [
        PythonSymbol(
            kind="module",
            name=path.stem,
            qualified_name=module_name,
            file_path=relative,
            start_line=1,
            end_line=len(source.splitlines()),
            signature=module_name,
            docstring=ast.get_docstring(tree),
            source=source,
            imports=imports,
            calls=[],
        )
    ]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(_symbol(node, "class", module_name, relative, source, imports))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(
                        _symbol(
                            child,
                            "function",
                            f"{module_name}.{node.name}",
                            relative,
                            source,
                            imports,
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_symbol(node, "function", module_name, relative, source, imports))
    return symbols


def parse_repository(root: Path) -> list[PythonSymbol]:
    return [
        symbol for path in sorted(root.rglob("*.py")) for symbol in parse_python_file(path, root)
    ]


def symbols_to_evidence(symbols: list[PythonSymbol], repository_root: Path) -> list[EvidenceItem]:
    evidence = []
    for symbol in symbols:
        compact = (
            f"Python {symbol.kind}: {symbol.qualified_name}\n"
            f"Signature: {symbol.signature}\n"
            f"Imports: {', '.join(symbol.imports) or 'none'}\n"
            f"Direct calls: {', '.join(symbol.calls) or 'none'}\n"
            f"Docstring: {symbol.docstring or 'none'}\n"
            f"Source:\n{symbol.source}"
        )
        chunk_id = f"code:{symbol.qualified_name}"
        service = _service_for_path(symbol.file_path)
        citation = f"[{symbol.file_path}:{symbol.name}]"
        evidence.append(
            EvidenceItem(
                citation_id=citation,
                chunk_id=chunk_id,
                source_id="shopflow_api",
                source_path=(repository_root / symbol.file_path).as_posix(),
                source_type="code",
                section=symbol.qualified_name,
                text=compact,
                entity_ids=[symbol.qualified_name, *symbol.calls],
                service=service,
                content_hash=content_hash(compact),
            )
        )
    return evidence


def code_graph(symbols: list[PythonSymbol]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    nodes: dict[str, list[dict]] = {
        key: [] for key in ("Repository", "Module", "Class", "Function", "Service")
    }
    edges: dict[str, list[dict]] = {
        key: []
        for key in (
            "CONTAINS",
            "DEFINES",
            "IMPORTS",
            "CALLS",
            "BELONGS_TO_SERVICE",
            "EXPOSES_ENDPOINT",
        )
    }
    nodes["Repository"].append({"id": "shopflow_api", "name": "ShopFlow FastAPI demo repository"})
    known_functions = {symbol.qualified_name for symbol in symbols if symbol.kind == "function"}
    known_modules = {symbol.qualified_name for symbol in symbols if symbol.kind == "module"}
    services: set[str] = set()
    for symbol in symbols:
        label = {"module": "Module", "class": "Class", "function": "Function"}[symbol.kind]
        nodes[label].append(
            {
                "id": symbol.qualified_name,
                "name": symbol.name,
                "path": symbol.file_path,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "chunk_id": f"code:{symbol.qualified_name}",
            }
        )
        module_id = symbol.file_path.removesuffix(".py").replace("/", ".")
        if symbol.kind == "module":
            edges["CONTAINS"].append(_edge("shopflow_api", symbol.qualified_name))
        else:
            edges["DEFINES"].append(_edge(module_id, symbol.qualified_name))
        service = _service_for_path(symbol.file_path)
        if service:
            services.add(service)
            edges["BELONGS_TO_SERVICE"].append(_edge(symbol.qualified_name, service))
        for imported in symbol.imports if symbol.kind == "module" else []:
            target = next((module for module in known_modules if module.endswith(imported)), None)
            if target:
                edges["IMPORTS"].append(_edge(symbol.qualified_name, target))
        for call in symbol.calls:
            local = f"{module_id}.{call}"
            # Resolve local free functions only; imports and receivers need binding analysis.
            candidates = {local} & known_functions if "." not in call else set()
            if len(candidates) == 1:
                edges["CALLS"].append(_edge(symbol.qualified_name, candidates.pop()))
        if symbol.endpoint:
            edges["EXPOSES_ENDPOINT"].append(
                _edge(symbol.qualified_name, service or "APIService", route=symbol.endpoint)
            )
    nodes["Service"].extend({"id": service, "name": service} for service in sorted(services))
    return nodes, edges


def _symbol(
    node: ast.AST, kind: str, parent: str, path: str, source: str, imports: list[str]
) -> PythonSymbol:
    name = node.name  # type: ignore[attr-defined]
    source_text = ast.get_source_segment(source, node) or ""
    endpoint = None
    for decorator in getattr(node, "decorator_list", []):
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr.lower() in {"get", "post", "put", "delete", "patch"}
            and decorator.args
        ):
            endpoint = (
                ast.literal_eval(decorator.args[0])
                if isinstance(decorator.args[0], ast.Constant)
                else None
            )
    return PythonSymbol(
        kind=kind,
        name=name,
        qualified_name=f"{parent}.{name}",
        file_path=path,
        start_line=getattr(node, "lineno", 1),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        signature=_signature(node),
        docstring=ast.get_docstring(node),
        source=source_text,
        imports=imports,
        calls=sorted(
            {_call_name(child) for child in ast.walk(node) if isinstance(child, ast.Call)} - {""}
        ),
        endpoint=endpoint,
    )


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = [argument.arg for argument in node.args.args]
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        return f"{prefix}def {node.name}({', '.join(arguments)})"
    return ""


def _imports(tree: ast.AST) -> list[str]:
    values = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            values.append(node.module or "")
    return sorted(set(filter(None, values)))


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return ast.unparse(node.func)
    return ""


def _service_for_path(path: str) -> str | None:
    mapping = {
        "orders": "OrderService",
        "payments": "PaymentService",
        "refunds": "RefundService",
        "authentication": "AuthenticationService",
        "uploads": "UploadService",
        "coupons": "CouponService",
    }
    return next((service for name, service in mapping.items() if name in path), None)


def _edge(from_id: str, to_id: str, **properties: str) -> dict:
    return {"from_id": from_id, "to_id": to_id, "properties": properties}
