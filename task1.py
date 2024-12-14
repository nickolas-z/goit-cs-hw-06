import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
import shutil

logger = logging.getLogger(__name__)

# Global flag to track if the program has already received Ctrl+C
interrupted = False


def handle_sigint(signum, frame):
    global interrupted
    if not interrupted:
        interrupted = True
        logger.info("Operation cancelled (Ctrl+C). Exiting the application.")
    sys.exit(0)


# Register the signal handler for Ctrl+C
signal.signal(signal.SIGINT, handle_sigint)


async def get_unique_filename(dest_dir: Path, filename: str) -> str:
    """
    Generates a unique filename in the given directory
    if a file with that name already exists.

    Args:
        dest_dir (Path): Destination directory as a Path object.
        filename (str): The original filename.

    Returns:
        str: A unique filename that doesn't conflict with existing files
            in the destination directory.
    """
    base_name = dest_dir / filename
    if not await asyncio.to_thread(base_name.exists):
        return filename

    stem = base_name.stem
    suffix = base_name.suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem}_{counter}{suffix}"
        if not await asyncio.to_thread(candidate.exists):
            return candidate.name
        counter += 1


async def copy_file(
    file_path: Path,
    dest_dir: Path,
    semaphore: asyncio.Semaphore,
    dry_run: bool = False,
) -> None:
    """
    Copies the specified file to a subdirectory in the destination directory
    named after the file's extension. A semaphore limits the number of
    concurrent operations.
    If `dry_run` is True, the file is not actually copied.

    Args:
        file_path (Path): The source file path.
        dest_dir (Path): The destination directory.
        semaphore (Semaphore): Semaphore to limit concurrent operations.
        dry_run (bool, optional): If True, the operation is simulated without
            copying the file. Defaults to False.

    Returns:
        None
    """
    async with semaphore:
        try:
            file_extension = file_path.suffix.lstrip(".")
            if not file_extension:
                file_extension = "no_extension"

            new_dir = dest_dir / file_extension

            # Attempt to create the target directory
            try:
                await asyncio.to_thread(
                    new_dir.mkdir, exist_ok=True, parents=True
                )
            except PermissionError as e:
                logger.error(
                    f"Permission error creating directory {new_dir}: {e}"
                )
                return
            except Exception as e:
                logger.error(
                    f"Unknown error creating directory {new_dir}: {e}"
                )
                return

            # Generate a unique filename to avoid overwriting
            new_file_name = await get_unique_filename(new_dir, file_path.name)
            new_file_path = new_dir / new_file_name

            if dry_run:
                logger.info(
                    f"[DRY-RUN] Would copy {file_path} to {new_file_path}"
                )
                return

            # Attempt to copy the file
            try:
                await asyncio.to_thread(shutil.copy2, file_path, new_file_path)
                logger.info(f"Copied {file_path} to {new_file_path}")
            except PermissionError as e:
                logger.error(
                    f"Access denied copying {
                        file_path} to {new_file_path}: {e}"
                )
            except Exception as e:
                logger.error(
                    f"Unknown error copying {
                        file_path} to {new_file_path}: {e}"
                )

        except Exception as e:
            logger.error(f"Unexpected error in copy_file for {file_path}: {e}")


async def read_folder(
    source_dir: Path,
    dest_dir: Path,
    semaphore: asyncio.Semaphore,
    dry_run: bool = False,
) -> None:
    """
    Recursively reads the source directory and copies all found files to the
    destination directory, sorting them by their extension into subdirectories.
    Uses a semaphore to control concurrency.
    If `dry_run` is True, no actual copying takes place.

    Args:
        source_dir (Path): The source directory to read files from.
        dest_dir (Path): The destination directory to copy files into.
        semaphore (asyncio.Semaphore): Semaphore to limit concurrent operations
        dry_run (bool, optional): If True, simulate operations without
            copying files. Defaults to False.

    Returns:
        None

    Raises:
        PermissionError: If access to source directory or a file is denied.
    """
    try:
        items = await asyncio.to_thread(lambda: list(source_dir.iterdir()))
    except PermissionError as e:
        logger.error(f"Access denied reading directory {source_dir}: {e}")
        return
    except Exception as e:
        logger.error(f"Unknown error reading directory {source_dir}: {e}")
        return

    tasks = []
    for item in items:
        try:
            is_dir = await asyncio.to_thread(item.is_dir)
            is_file = await asyncio.to_thread(item.is_file)
        except Exception as e:
            logger.error(f"Error checking type of {item}: {e}")
            continue

        if is_dir:
            tasks.append(read_folder(item, dest_dir, semaphore, dry_run))
        elif is_file:
            tasks.append(copy_file(item, dest_dir, semaphore, dry_run))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    """
    The main asynchronous entry point of the program. It parses command-line
    arguments,     sets up logging, checks directories, and then starts
    recursive file copying.

    Args:
        None

    Returns:
        None
    """
    parser = argparse.ArgumentParser(
        description="Asynchronous recursive file sorter by extension."
    )
    parser.add_argument("source_dir", help="Path to the source directory")
    parser.add_argument(
        "dest_dir",
        nargs="?",
        default="dst",
        help="Path to the destination directory (default: dist)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a dry run without actually copying any files.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO).",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=10,
        help="Limit the number of concurrent file copy operations.",
    )
    parser.add_argument(
        "--path-to-log",
        default="task1.log",
        help="Path to the log file (default: application.log).",
    )
    args = parser.parse_args()

    # Set logging level based on argument
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(args.path_to_log, mode="a", encoding="utf-8"),
        ],
    )

    source_dir = Path(args.source_dir).resolve()
    dest_dir = Path(args.dest_dir).resolve()

    if not await asyncio.to_thread(source_dir.exists):
        logger.error(f"Directory {source_dir} does not exist.")
        return
    if not await asyncio.to_thread(source_dir.is_dir):
        logger.error(f"{source_dir} is not a directory.")
        return

    # Create the destination directory if it does not exist
    try:
        await asyncio.to_thread(dest_dir.mkdir, exist_ok=True, parents=True)
    except PermissionError as e:
        logger.error(
            f"Permission error creating destination directory {dest_dir}: {e}"
        )
        return
    except Exception as e:
        logger.error(
            f"Unknown error creating destination directory {dest_dir}: {e}"
        )
        return

    logger.info(
        f"Starting copying from {source_dir} to {
            dest_dir}. Dry run: {args.dry_run}"
    )
    semaphore = asyncio.Semaphore(args.max_concurrency)
    await read_folder(source_dir, dest_dir, semaphore, dry_run=args.dry_run)
    logger.info("Operation completed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except EOFError:
        logger.error("Input ended unexpectedly. Exiting the application.")
    except KeyboardInterrupt:
        logger.info("Operation cancelled (Ctrl+C). Exiting the application.")
