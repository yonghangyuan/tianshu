"""E2E 测试：连接真实的 MCP filesystem server。"""
import asyncio
import os
import tempfile
import sys

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mcp import Client, StdioServerParameters


async def main():
    tmpdir = tempfile.mkdtemp()
    print(f'Test dir: {tmpdir}')

    # Write a test file
    test_file = os.path.join(tmpdir, 'hello.txt')
    with open(test_file, 'w') as f:
        f.write('Hello from Tianshu MCP!')

    # Connect to filesystem MCP server via stdio
    server_params = StdioServerParameters(
        command='npx',
        args=['-y', '@modelcontextprotocol/server-filesystem', tmpdir],
    )

    print('Connecting to MCP filesystem server...')
    async with Client(server_params) as client:
        # Discover tools
        tools = await client.list_tools()
        print(f'\n✅ Discovered {len(tools)} tools:')
        for t in tools:
            desc = getattr(t, 'description', '') or ''
            print(f'  - {t.name}: {desc[:100]}')

        # Call read_file
        print(f'\n📖 Calling read_file({test_file})...')
        result = await client.call_tool('read_file', {'path': test_file})
        print('Result:')
        for c in result.content:
            if hasattr(c, 'text'):
                print(f'  {c.text.strip()}')

        # Call list_directory
        print(f'\n📂 Calling list_directory({tmpdir})...')
        result = await client.call_tool('list_directory', {'path': tmpdir})
        print('Result:')
        for c in result.content:
            if hasattr(c, 'text'):
                print(f'  {c.text.strip()[:300]}')

    # Cleanup
    os.remove(test_file)
    os.rmdir(tmpdir)
    print('\n✅ E2E test passed!')


if __name__ == '__main__':
    asyncio.run(main())
