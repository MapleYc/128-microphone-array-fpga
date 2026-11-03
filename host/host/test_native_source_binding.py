"""Reject changed external binaries and builds from different source inputs."""
from pathlib import Path
import os,shutil,tempfile,unittest
from unittest.mock import patch
import native_paths
import structured_store as store

class NativeBuildIntegrity(unittest.TestCase):
    def copy_build(self,destination):
        source,record=native_paths.verify_native()
        for name in native_paths.ARTIFACTS:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
        store.dump_file(destination/'native_manifest.yaml',record)
        return record

    def test_binary_tamper_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory(prefix='native_guard_') as folder:
            destination=Path(folder);self.copy_build(destination)
            binary=destination/'bin/frame_kernel.dll';binary.write_bytes(binary.read_bytes()+b'changed')
            with patch.dict(os.environ,{'HOST_NATIVE_BUILD_DIR':str(destination)}):
                with self.assertRaisesRegex(ValueError,'Native binary changed'):native_paths.verify_native()
                with self.assertRaisesRegex(ValueError,'Native binary changed'):native_paths.frame_library()

    def test_build_from_other_source_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='native_guard_') as folder:
            destination=Path(folder);record=self.copy_build(destination)
            first=next(iter(record['source_bindings']));record['source_bindings'][first]='0'*64
            store.dump_file(destination/'native_manifest.yaml',record)
            with patch.dict(os.environ,{'HOST_NATIVE_BUILD_DIR':str(destination)}):
                with self.assertRaisesRegex(ValueError,'does not match this source'):native_paths.verify_native()

    def test_selection_change_and_explicit_recheck_update_library_paths(self):
        original=native_paths.frame_library()
        with tempfile.TemporaryDirectory(prefix='native_guard_') as folder:
            destination=Path(folder);self.copy_build(destination)
            with patch.dict(os.environ,{'HOST_NATIVE_BUILD_DIR':str(destination)}):
                self.assertEqual(native_paths.frame_library(),destination/'bin/frame_kernel.dll')
                native_paths.verify_native()
                self.assertEqual(native_paths.post_library(),destination/'bin/libstream_post.dll')
        self.assertEqual(native_paths.frame_library(),original)

if __name__=='__main__':unittest.main()
