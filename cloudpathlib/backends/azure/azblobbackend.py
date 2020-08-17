from datetime import datetime
import os
from pathlib import PurePosixPath

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from ..base import Backend
import cloudpathlib


class AzureBlobBackend(Backend):
    path_class = cloudpathlib.AzureBlobPath

    def __init__(self, blob_service_client=None):
        """

        Parameters
        ----------
        blob_service_client : BlobServiceClient, optional
            If you need to instantiate the BlobServiceClient in any way
            that
        """
        if blob_service_client is None:
            self.service_client = BlobServiceClient.from_connection_string(
                os.getenv("AZURE_STORAGE_CONNECTION_STRING")
            )

    def get_metadata(self, cloud_path):
        blob = self.service_client.get_blob_client(
            container=cloud_path.container, blob=cloud_path.blob
        )
        properties = blob.get_blob_properties()

        return properties

    def download_file(self, cloud_path, local_path):
        blob = self.service_client.get_blob_client(
            container=cloud_path.container, blob=cloud_path.blob
        )

        download_stream = blob.download_blob()
        local_path.write_bytes(download_stream.readall())

        return local_path

    def is_file_or_dir(self, cloud_path):
        # short-circuit the root-level container
        if not cloud_path.blob:
            return "dir"

        try:
            self.get_metadata(cloud_path)
            return "file"
        except ResourceNotFoundError:
            prefix = cloud_path.blob
            if prefix and not prefix.endswith("/"):
                prefix += "/"

            # not a file, see if it is a directory
            container_client = self.service_client.get_container_client(cloud_path.container)

            try:
                next(container_client.list_blobs(name_starts_with=prefix))
                return "dir"
            except StopIteration:
                return None

    def exists(self, cloud_path):
        return self.is_file_or_dir(cloud_path) in ["file", "dir"]

    def list_dir(self, cloud_path, recursive=False):
        container_client = self.service_client.get_container_client(cloud_path.container)

        prefix = cloud_path.blob
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        yielded_dirs = set()

        # NOTE: Not recursive may be slower than necessary since it just filters
        #   the recursive implementation
        for o in container_client.list_blobs(name_starts_with=prefix):
            # get directory from this path
            for parent in PurePosixPath(o.name[len(prefix) :]).parents:
                parent = str(parent)

                # if we haven't surfaced thei directory already
                if parent not in yielded_dirs and parent != ".":

                    # skip if not recursive and this is beyond our depth
                    if not recursive and "/" in parent[len(prefix) :]:
                        continue

                    yield self.path_class(
                        f"az://{cloud_path.container}/{prefix}{parent}",
                        backend=self,
                        local_cache_dir=cloud_path._local_cache_dir,
                    )
                    yielded_dirs.add(parent)

            # skip file if not recursive and this is beyond our depth
            if not recursive and "/" in o.name[len(prefix) :]:
                continue

            yield self.path_class(
                f"az://{cloud_path.container}/{o.name}",
                backend=self,
                local_cache_dir=cloud_path._local_cache_dir,
            )

    def move_file(self, src, dst):
        # just a touch, so "REPLACE" metadata
        if src == dst:
            blob_client = self.service_client.get_blob_client(
                container=src.container, blob=src.blob
            )

            blob_client.set_blob_metadata(
                metadata=dict(last_modified=str(datetime.utcnow().timestamp()))
            )

        else:
            target = self.service_client.get_blob_client(container=dst.container, blob=dst.blob)

            source = self.service_client.get_blob_client(container=src.container, blob=src.blob)

            target.start_copy_from_url(source.url)

            self.remove(src)

        return dst

    def remove(self, cloud_path):
        if self.is_file_or_dir(cloud_path) == "dir":
            blobs = [b.blob for b in self.list_dir(cloud_path, recursive=True)]
            container_client = self.service_client.get_container_client(cloud_path.container)
            container_client.delete_blobs(*blobs)
        elif self.is_file_or_dir(cloud_path) == "file":
            blob = self.service_client.get_blob_client(
                container=cloud_path.container, blob=cloud_path.blob
            )

            blob.delete_blob()

        return cloud_path

    def upload_file(self, local_path, cloud_path):
        blob = self.service_client.get_blob_client(
            container=cloud_path.container, blob=cloud_path.blob
        )

        blob.upload_blob(local_path.read_bytes(), overwrite=True)

        return cloud_path