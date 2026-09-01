from typing import Any, Dict, List, Optional
from neo4j import GraphDatabase, Driver
from src.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


class Neo4jConnection:
    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: str = NEO4J_PASSWORD,
    ):
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        if self._driver is not None:
            self._driver.close()

    def execute_query(
        self, cypher: str, parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Execute a read/general Cypher query and return list of result records as dicts."""
        with self._driver.session() as session:
            result = session.run(cypher, parameters or {})
            return [record.data() for record in result]

    def execute_write(
        self, cypher: str, parameters: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Execute a write transaction in Cypher."""
        with self._driver.session() as session:
            return session.execute_write(
                lambda tx: tx.run(cypher, parameters or {}).consume()
            )

    def init_schema(self):
        """Create uniqueness constraints for Neo4j entities."""
        constraints = [
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT document_node_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT chunk_node_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT asset_node_id IF NOT EXISTS FOR (a:Asset) REQUIRE a.id IS UNIQUE",
        ]
        with self._driver.session() as session:
            for query in constraints:
                session.run(query)
        print("Neo4j constraints successfully created.")


_neo4j_conn: Optional[Neo4jConnection] = None


def get_neo4j_connection() -> Neo4jConnection:
    global _neo4j_conn
    if _neo4j_conn is None:
        _neo4j_conn = Neo4jConnection()
    return _neo4j_conn


def init_neo4j():
    conn = get_neo4j_connection()
    conn.init_schema()
