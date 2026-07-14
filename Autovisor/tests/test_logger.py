# encoding=utf-8

import sys
import threading
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.logger import Logger

sys.path.remove(_AUTOVISOR_ROOT)


def _restore_default(logger):
    logger.configure(None, force=True, clear=True)


def test_account_can_reconfigure_filename_before_first_write(tmp_path):
    logger = Logger()
    try:
        first_path = logger.configure(
            None,
            log_dir=tmp_path,
            force=True,
            clear=True,
        )
        account_path = logger.configure("7", log_dir=tmp_path)

        assert account_path != first_path
        assert "Account_7" in Path(account_path).name
        assert Path(account_path).parent == tmp_path.resolve()
    finally:
        _restore_default(logger)


def test_existing_log_is_not_silently_retargeted(tmp_path):
    logger = Logger()
    try:
        original_path = logger.configure(
            "1",
            log_dir=tmp_path,
            force=True,
            clear=True,
        )
        logger.write_log("first entry\n")

        retained_path = logger.configure("2", log_dir=tmp_path)

        assert retained_path == original_path
        assert "first entry" in Path(original_path).read_text(encoding="utf-8")
    finally:
        _restore_default(logger)


def test_concurrent_writes_are_complete_and_immediately_persisted(tmp_path):
    logger = Logger()
    try:
        log_path = Path(
            logger.configure(
                "threads",
                log_dir=tmp_path,
                force=True,
                clear=True,
            )
        )

        def worker(worker_id):
            for item_id in range(40):
                logger.write_log(f"worker={worker_id},item={item_id}\n")

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 240
        assert all("worker=" in line and ",item=" in line for line in lines)
        assert logger._last_write_error is None
    finally:
        _restore_default(logger)


def test_save_keeps_incremental_log_and_default_directory_is_stable(tmp_path):
    logger = Logger()
    try:
        log_path = Path(
            logger.configure(
                None,
                log_dir=tmp_path,
                force=True,
                clear=True,
            )
        )
        logger.write_log("durable\n")
        before_save = log_path.read_text(encoding="utf-8")

        logger.save(inform=False)

        assert log_path.read_text(encoding="utf-8") == before_save
    finally:
        default_path = Path(
            logger.configure(None, force=True, clear=True)
        )

    assert default_path.parent == Path(_AUTOVISOR_ROOT) / "logs"
