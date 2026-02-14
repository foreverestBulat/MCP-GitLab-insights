from mcp_main import project_health_report, read_project_readme
import asyncio


# result = asyncio.run(project_health_report("1"))

result = asyncio.run(read_project_readme("1", "develop"))

print(result)