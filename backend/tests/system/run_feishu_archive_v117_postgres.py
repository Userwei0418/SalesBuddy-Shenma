"""Exercise the unchanged one-time V117 entry point in its exact isolated schema."""
import asyncio
from run_feishu_archive_v108_postgres import main

if __name__ == '__main__':
    asyncio.run(main(version=117))
