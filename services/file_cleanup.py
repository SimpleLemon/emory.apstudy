import logging
import os
from datetime import datetime

from extensions import db
from models import SharedFile

logger = logging.getLogger(__name__)


def cleanup_expired_files():
    from flask import current_app

    now = datetime.utcnow()
    expired_files = SharedFile.query.filter(SharedFile.expires_at <= now).all()
    deleted_count = 0
    upload_dir = current_app.config["FILE_SHARE_UPLOAD_DIR"]

    for shared_file in expired_files:
        absolute_path = os.path.join(upload_dir, shared_file.stored_path)
        try:
            os.remove(absolute_path)
        except FileNotFoundError:
            logger.info("Missing expired shared file on disk: %s", absolute_path)
        except OSError:
            logger.exception("Failed to delete expired shared file on disk: %s", absolute_path)

        db.session.delete(shared_file)
        deleted_count += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to commit expired file cleanup.")
        raise

    logger.info("Deleted %s expired shared file(s).", deleted_count)
    return deleted_count