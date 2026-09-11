from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import GraphDatabase

from app.config import Settings

ALLOWED_LABELS = {
    "Customer",
    "Ticket",
    "ErrorCode",
    "Feature",
    "Service",
    "Incident",
    "Resolution",
    "Policy",
    "Repository",
    "Module",
    "Class",
    "Function",
    "EvidenceChunk",
}
ALLOWED_RELATIONSHIPS = {
    "CREATED",
    "MENTIONS",
    "AFFECTS",
    "OWNED_BY",
    "CAUSED_BY",
    "RESOLVED_BY",
    "GOVERNED_BY",
    "IMPLEMENTED_BY",
    "RELATED_TO",
    "CONTAINS",
    "DEFINES",
    "IMPORTS",
    "CALLS",
    "BELONGS_TO_SERVICE",
    "EXPOSES_ENDPOINT",
    "SUPPORTED_BY",
}


def validate_schema(label: str, relationship: str | None = None) -> None:
    if label not in ALLOWED_LABELS:
        raise ValueError(f"Unsupported Neo4j label: {label}")
    if relationship and relationship not in ALLOWED_RELATIONSHIPS:
        raise ValueError(f"Unsupported Neo4j relationship: {relationship}")


class Neo4jStore:
    def __init__(self, settings: Settings) -> None:
        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD.get_secret_value()),
            connection_timeout=4,
        )

    def close(self) -> None:
        self.driver.close()

    def health(self) -> bool:
        self.driver.verify_connectivity()
        return True

    def ensure_constraints(self) -> None:
        with self.driver.session() as session:
            for label in ALLOWED_LABELS:
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )

    def clear_demo(self) -> None:
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def upsert_nodes(self, label: str, nodes: Iterable[dict[str, Any]]) -> None:
        validate_schema(label)
        with self.driver.session() as session:
            session.run(
                f"UNWIND $nodes AS row MERGE (n:{label} {{id: row.id}}) SET n += row",
                nodes=list(nodes),
            )

    def upsert_relationships(self, relationship: str, edges: Iterable[dict[str, Any]]) -> None:
        validate_schema("EvidenceChunk", relationship)
        query = (
            "UNWIND $edges AS row MATCH (a {id: row.from_id}), (b {id: row.to_id}) "
            f"MERGE (a)-[r:{relationship}]->(b) SET r += row.properties"
        )
        with self.driver.session() as session:
            session.run(query, edges=list(edges))

    def get_ticket_neighborhood(self, ticket_id: str, max_depth: int = 2) -> dict[str, Any]:
        return self._bounded_neighborhood("Ticket", ticket_id, max_depth)

    def get_error_context(self, error_code: str, max_depth: int = 2) -> dict[str, Any]:
        return self._bounded_neighborhood("ErrorCode", error_code, max_depth)

    def get_service_impact(self, service_name: str, max_depth: int = 2) -> dict[str, Any]:
        return self._bounded_neighborhood("Service", service_name, max_depth)

    def get_function_call_context(self, function_name: str, max_depth: int = 2) -> dict[str, Any]:
        return self._bounded_neighborhood("Function", function_name, max_depth)

    def get_incident_resolution_paths(self, service_name: str) -> dict[str, Any]:
        query = """
        MATCH p=(s:Service {id: $value})-[:AFFECTS|RELATED_TO|RESOLVED_BY*1..3]-(n)
        WHERE n:Incident OR n:Resolution
        RETURN p LIMIT 30
        """
        return self._paths(query, value=service_name)

    def _bounded_neighborhood(self, label: str, value: str, max_depth: int) -> dict[str, Any]:
        validate_schema(label)
        depth = max(1, min(max_depth, 2))
        query = f"MATCH p=(start:{label} {{id: $value}})-[*1..{depth}]-(related) RETURN p LIMIT 40"
        return self._paths(query, value=value)

    def _paths(self, query: str, **parameters: Any) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        with self.driver.session() as session:
            for record in session.run(query, **parameters):
                path = record["p"]
                for node in path.nodes:
                    identifier = str(node.get("id"))
                    nodes[identifier] = {
                        "id": identifier,
                        "labels": list(node.labels),
                        "name": node.get("name", identifier),
                        "chunk_id": node.get("chunk_id"),
                    }
                for rel in path.relationships:
                    key = str(rel.element_id)
                    edges[key] = {
                        "source": str(rel.start_node.get("id")),
                        "target": str(rel.end_node.get("id")),
                        "type": rel.type,
                        "chunk_id": rel.get("chunk_id"),
                        "confidence": rel.get("confidence"),
                    }
        return {"nodes": list(nodes.values())[:50], "edges": list(edges.values())[:80]}
