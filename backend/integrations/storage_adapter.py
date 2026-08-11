"""BYOS storage integration adapter: exposes a user-connected S3-compatible bucket
through the Tool Broker's policy boundary (storage.read / storage.write)."""
from __future__ import annotations

from backend.integrations.base import IntegrationAdapter, IntegrationError
from backend.storage.base import StorageError
from backend.storage.factory import storage_for_connection


class S3ByosAdapter(IntegrationAdapter):
    integration_type = "s3"

    def capabilities(self) -> list[str]:
        return ["storage.read", "storage.write"]

    def _storage(self):
        return storage_for_connection(self.connection)

    async def health_check(self) -> bool:
        try:
            return await self._storage().health_check()
        except Exception:
            return False

    async def read(self, operation: str, params: dict) -> dict:
        storage = self._storage()
        try:
            if operation == "list":
                return {"keys": await storage.list(params.get("prefix", ""))}
            if operation == "stat":
                st = await storage.stat(params["path"])
                return {"path": st.path, "size_bytes": st.size_bytes,
                        "modified_at": st.modified_at.isoformat() if st.modified_at else None,
                        "checksum": st.checksum}
            if operation == "read":
                data = await storage.read(params["path"])
                if len(data) > 5 * 1024 * 1024:
                    raise IntegrationError("file too large for inline read (>5MB)")
                return {"path": params["path"], "content_base64": __import__("base64").b64encode(data).decode()}
        except StorageError as e:
            raise IntegrationError(str(e)) from e
        raise IntegrationError(f"unknown read operation: {operation}")

    async def execute(self, operation: str, params: dict) -> dict:
        storage = self._storage()
        try:
            if operation == "write":
                import base64

                data = base64.b64decode(params["content_base64"])
                st = await storage.write(params["path"], data)
                return {"status": "SUCCESS", "path": st.path, "size_bytes": st.size_bytes}
            if operation == "delete":
                await storage.delete(params["path"])
                return {"status": "SUCCESS", "path": params["path"]}
        except StorageError as e:
            raise IntegrationError(str(e)) from e
        raise IntegrationError(f"unknown execute operation: {operation}")
