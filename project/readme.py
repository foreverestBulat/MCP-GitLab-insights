from typing import Optional, Dict, Any
import httpx
import base64
from mcp.server.fastmcp import FastMCP
# from mcp.server.fastmcp import ro

# mcp = FastMCP("gitlab-readme-reader")

# # Конфигурация (замените на вашу)
# GITLAB_URL = "http://localhost"
# GITLAB_TOKEN = "ваш_токен_здесь"

# HEADERS = {
#     "PRIVATE-TOKEN": GITLAB_TOKEN,
#     "Content-Type": "application/json"
# }
def register_readme_tools(mcp: FastMCP):
    
    async def get_project_readme_content(project_id: str, ref: str = "main") -> Optional[Dict[str, Any]]:
        """
        Получить содержимое README файла проекта
        
        Args:
            project_id: ID или путь к проекту (например: 'namespace/project' или 123)
            ref: ветка/тег (по умолчанию: main)
        
        Returns:
            Словарь с содержимым README или None при ошибке
        """
        try:
            # Сначала ищем README файлы в корне проекта
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Получаем список файлов в корне репозитория
                tree_response = await client.get(
                    f"{GITLAB_URL}/api/v4/projects/{httpx.quote(str(project_id), safe='')}/repository/tree",
                    headers=HEADERS,
                    params={"ref": ref, "path": "", "per_page": 100}
                )
                
                if tree_response.status_code == 404:
                    # Попробуем найти проект по другому идентификатору
                    return None
                
                tree_response.raise_for_status()
                files = tree_response.json()
                
                # Ищем README файлы (разные расширения)
                readme_files = [
                    f for f in files 
                    if f['name'].lower().startswith('readme') 
                    and f['type'] == 'blob'
                ]
                
                if not readme_files:
                    return {"status": "not_found", "message": "README файл не найден в корне проекта"}
                
                # Берем основной README (обычно без расширения или .md)
                readme_file = None
                for rf in readme_files:
                    name_lower = rf['name'].lower()
                    if name_lower == 'readme' or name_lower == 'readme.md':
                        readme_file = rf
                        break
                
                if not readme_file:
                    readme_file = readme_files[0]  # Берем первый найденный
                
                # Получаем содержимое файла
                file_response = await client.get(
                    f"{GITLAB_URL}/api/v4/projects/{httpx.quote(str(project_id), safe='')}/repository/files/{httpx.quote(readme_file['path'])}",
                    headers=HEADERS,
                    params={"ref": ref}
                )
                
                file_response.raise_for_status()
                file_data = file_response.json()
                
                # Декодируем base64 содержимое
                content = base64.b64decode(file_data['content']).decode('utf-8')
                
                return {
                    "status": "success",
                    "project_id": project_id,
                    "filename": readme_file['name'],
                    "path": readme_file['path'],
                    "ref": ref,
                    "content": content,
                    "size_bytes": len(content),
                    "encoding": file_data.get('encoding', 'base64'),
                    "last_commit_id": file_data.get('last_commit_id')
                }
                
        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"HTTP ошибка: {e.response.status_code}",
                "details": str(e)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Ошибка при получении README: {str(e)}"
            }

    @mcp.tool()
    async def read_project_readme(project_identifier: str, branch: str = "main", max_length: Optional[int] = 5000) -> str:
        """
        Прочитать и показать содержимое README файла GitLab проекта
        
        Args:
            project_identifier: ID проекта (число) или путь (namespace/project)
            branch: Ветка для чтения (по умолчанию: main)
            max_length: Максимальная длина вывода (None - без ограничений)
        
        Returns:
            Отформатированное содержимое README или сообщение об ошибке
        """
        result = await get_project_readme_content(project_identifier, branch)
        
        if result is None:
            return f"❌ Проект '{project_identifier}' не найден или недоступен"
        
        if result['status'] == 'error':
            return f"❌ Ошибка: {result['message']}\nДетали: {result.get('details', 'нет')}"
        
        if result['status'] == 'not_found':
            return f"📄 README не найден в проекте '{project_identifier}' на ветке '{branch}'"
        
        content = result['content']
        filename = result['filename']
        
        # Обрезаем контент если нужно
        if max_length and len(content) > max_length:
            content = content[:max_length] + f"\n\n... [сокращено, всего {len(result['content'])} символов]"
        
        return f"""
    📖 **README ФАЙЛ ПРОЕКТА**
    **Проект:** {project_identifier}
    **Файл:** {filename}
    **Ветка:** {branch}
    **Размер:** {result['size_bytes']} символов
    **Последний коммит:** {result.get('last_commit_id', 'неизвестно')[:8] if result.get('last_commit_id') else 'неизвестно'}

    ────────────────────────────────────
    {content}
    ────────────────────────────────────

    ℹ️ Для полной версии используйте max_length=None
    """

    @mcp.tool()
    async def find_all_readme_files(project_identifier: str, ref: str = "main") -> str:
        """
        Найти все README файлы в проекте (включая поддиректории)
        
        Args:
            project_identifier: ID или путь к проекту
            ref: Ветка/тег для поиска
        
        Returns:
            Список всех найденных README файлов
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Рекурсивно получаем все файлы
                response = await client.get(
                    f"{GITLAB_URL}/api/v4/projects/{httpx.quote(str(project_identifier), safe='')}/repository/tree",
                    headers=HEADERS,
                    params={"ref": ref, "recursive": "true", "per_page": 1000}
                )
                
                if response.status_code == 404:
                    return f"❌ Проект '{project_identifier}' не найден"
                
                response.raise_for_status()
                all_files = response.json()
                
                # Фильтруем README файлы
                readme_files = [
                    f for f in all_files 
                    if f['type'] == 'blob' and 'readme' in f['name'].lower()
                ]
                
                if not readme_files:
                    return f"📭 В проекте '{project_identifier}' не найдено README файлов"
                
                # Группируем по директориям
                readme_by_dir = {}
                for rf in readme_files:
                    dir_path = '/'.join(rf['path'].split('/')[:-1]) or '(корень)'
                    if dir_path not in readme_by_dir:
                        readme_by_dir[dir_path] = []
                    readme_by_dir[dir_path].append(rf['name'])
                
                # Форматируем результат
                result_lines = [f"📂 **НАЙДЕНО {len(readme_files)} README ФАЙЛОВ В ПРОЕКТЕ '{project_identifier}'**\n"]
                
                for dir_path, files in sorted(readme_by_dir.items()):
                    result_lines.append(f"\n📁 **{dir_path}:**")
                    for file_name in sorted(files):
                        result_lines.append(f"  • {file_name}")
                
                return "\n".join(result_lines)
                
        except Exception as e:
            return f"❌ Ошибка при поиске README файлов: {str(e)}"

    @mcp.tool()
    async def get_readme_stats(project_identifier: str, ref: str = "main") -> str:
        """
        Получить статистику по README файлам проекта
        
        Args:
            project_identifier: ID или путь к проекту
            ref: Ветка для анализа
        
        Returns:
            Статистика README файлов
        """
        try:
            # Получаем основной README
            main_readme = await get_project_readme_content(project_identifier, ref)
            
            # Получаем все README файлы
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GITLAB_URL}/api/v4/projects/{httpx.quote(str(project_identifier), safe='')}/repository/tree",
                    headers=HEADERS,
                    params={"ref": ref, "recursive": "true", "per_page": 1000}
                )
                
                if response.status_code == 404:
                    return f"❌ Проект '{project_identifier}' не найден"
                
                response.raise_for_status()
                all_files = response.json()
                
                # Фильтруем README файлы
                readme_files = [
                    f for f in all_files 
                    if f['type'] == 'blob' and 'readme' in f['name'].lower()
                ]
                
                # Анализ расширений
                extensions = {}
                for rf in readme_files:
                    name_parts = rf['name'].split('.')
                    if len(name_parts) > 1:
                        ext = name_parts[-1].lower()
                        extensions[ext] = extensions.get(ext, 0) + 1
                    else:
                        extensions['no_ext'] = extensions.get('no_ext', 0) + 1
                
                # Подсчет строк в основном README
                line_count = 0
                word_count = 0
                if main_readme and main_readme['status'] == 'success':
                    content = main_readme['content']
                    line_count = len(content.split('\n'))
                    word_count = len(content.split())
                
                result = f"""
    📊 **СТАТИСТИКА README ФАЙЛОВ**
    **Проект:** {project_identifier}
    **Ветка:** {ref}

    **Общая статистика:**
    • Всего README файлов: {len(readme_files)}
    • В корне проекта: {len([f for f in readme_files if '/' not in f['path']])}
    • В поддиректориях: {len([f for f in readme_files if '/' in f['path']])}

    **Расширения файлов:**
    """
                
                for ext, count in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
                    ext_name = ext if ext != 'no_ext' else '(без расширения)'
                    percentage = (count / len(readme_files)) * 100
                    result += f"• {ext_name}: {count} ({percentage:.1f}%)\n"
                
                if main_readme and main_readme['status'] == 'success':
                    result += f"""
    **Основной README ({main_readme['filename']}):**
    • Размер: {main_readme['size_bytes']} символов
    • Строк: {line_count}
    • Слов: {word_count}
    • Средняя длина строки: {main_readme['size_bytes'] / max(line_count, 1):.1f} символов
    """
                elif main_readme and main_readme['status'] == 'not_found':
                    result += f"\n⚠️ Основной README не найден в корне проекта"
                
                # Рекомендации
                if len(readme_files) == 0:
                    result += "\n🔴 **РЕКОМЕНДАЦИЯ:** Добавьте README файл в проект"
                elif len(readme_files) == 1 and 'md' not in extensions:
                    result += "\n🟡 **РЕКОМЕНДАЦИЯ:** Рассмотрите использование README.md для лучшего форматирования"
                
                return result
                
        except Exception as e:
            return f"❌ Ошибка при получении статистики: {str(e)}"

    @mcp.tool()
    async def search_in_readme(project_identifier: str, search_term: str, ref: str = "main", case_sensitive: bool = False) -> str:
        """
        Поиск текста в README файле проекта
        
        Args:
            project_identifier: ID или путь к проекту
            search_term: Текст для поиска
            ref: Ветка для поиска
            case_sensitive: Чувствительность к регистру
        
        Returns:
            Результаты поиска с контекстом
        """
        result = await get_project_readme_content(project_identifier, ref)
        
        if result is None or result['status'] != 'success':
            return f"❌ Не удалось прочитать README: {result.get('message', 'неизвестная ошибка') if result else 'проект не найден'}"
        
        content = result['content']
        search_in = content if case_sensitive else content.lower()
        term = search_term if case_sensitive else search_term.lower()
        
        if term not in search_in:
            return f"🔍 Текст '{search_term}' не найден в README файле проекта '{project_identifier}'"
        
        # Находим все вхождения
        lines = content.split('\n')
        matches = []
        
        for line_num, line in enumerate(lines, 1):
            search_line = line if case_sensitive else line.lower()
            if term in search_line:
                # Находим позицию в строке для выделения
                if case_sensitive:
                    pos = line.find(search_term)
                else:
                    pos = line.lower().find(term)
                
                # Берем контекст (символы вокруг)
                start = max(0, pos - 30)
                end = min(len(line), pos + len(search_term) + 30)
                
                context = line[start:end]
                if start > 0:
                    context = "..." + context
                if end < len(line):
                    context = context + "..."
                
                matches.append({
                    "line": line_num,
                    "context": context,
                    "position": pos
                })
        
        result_text = f"""
    🔍 **РЕЗУЛЬТАТЫ ПОИСКА В README**
    **Проект:** {project_identifier}
    **Поиск:** '{search_term}'
    **Чувствительность к регистру:** {'да' if case_sensitive else 'нет'}
    **Найдено совпадений:** {len(matches)}

    """
        
        if matches:
            result_text += "**Совпадения:**\n"
            for i, match in enumerate(matches[:10], 1):  # Ограничиваем 10 результатами
                result_text += f"{i}. Строка {match['line']}: {match['context']}\n"
            
            if len(matches) > 10:
                result_text += f"\n... и еще {len(matches) - 10} совпадений\n"
        
        result_text += f"\n📄 Файл: {result['filename']} ({result['size_bytes']} символов)"
        
        return result_text

    @mcp.tool()
    async def check_readme_quality(project_identifier: str, ref: str = "main") -> str:
        """
        Проверить качество README файла проекта
        
        Args:
            project_identifier: ID или путь к проекту
            ref: Ветка для проверки
        
        Returns:
            Отчет о качестве README
        """
        result = await get_project_readme_content(project_identifier, ref)
        
        if result is None or result['status'] != 'success':
            return f"❌ Не удалось прочитать README для проверки качества"
        
        content = result['content']
        lines = content.split('\n')
        
        # Критерии качества
        checks = {
            "has_title": False,
            "has_description": False,
            "has_installation": False,
            "has_usage": False,
            "has_license": False,
            "has_code_examples": False,
            "has_links": False,
            "has_images": False,
            "proper_length": False
        }
        
        content_lower = content.lower()
        
        # Проверки
        checks["has_title"] = any(line.strip().startswith('# ') for line in lines[:5])
        checks["has_description"] = len([l for l in lines if l.strip()]) > 5
        checks["has_installation"] = any(word in content_lower for word in ['install', 'setup', 'getting started'])
        checks["has_usage"] = any(word in content_lower for word in ['usage', 'example', 'how to use'])
        checks["has_license"] = 'license' in content_lower
        checks["has_code_examples"] = '```' in content or '`' in content
        checks["has_links"] = 'http://' in content or 'https://' in content or '[' in content
        checks["has_images"] = '![' in content or '.png' in content_lower or '.jpg' in content_lower
        checks["proper_length"] = 50 < len(content) < 10000
        
        # Подсчет баллов
        score = sum(checks.values())
        max_score = len(checks)
        percentage = (score / max_score) * 100
        
        # Оценка
        if percentage >= 80:
            rating = "🟢 ОТЛИЧНО"
        elif percentage >= 60:
            rating = "🟡 ХОРОШО"
        elif percentage >= 40:
            rating = "🟠 УДОВЛЕТВОРИТЕЛЬНО"
        else:
            rating = "🔴 ТРЕБУЕТ ДОРАБОТКИ"
        
        report = f"""
    📋 **АНАЛИЗ КАЧЕСТВА README**
    **Проект:** {project_identifier}
    **Файл:** {result['filename']}
    **Оценка:** {score}/{max_score} баллов ({percentage:.1f}%)
    **Рейтинг:** {rating}

    **ПРОВЕРКИ:**
    """
        
        check_descriptions = {
            "has_title": "Заголовок с #",
            "has_description": "Описание проекта",
            "has_installation": "Инструкция по установке",
            "has_usage": "Примеры использования",
            "has_license": "Указание лицензии",
            "has_code_examples": "Примеры кода",
            "has_links": "Ссылки на ресурсы",
            "has_images": "Изображения/диаграммы",
            "proper_length": "Оптимальная длина"
        }
        
        for check, description in check_descriptions.items():
            status = "✅" if checks[check] else "❌"
            report += f"{status} {description}\n"
        
        report += f"\n**РЕКОМЕНДАЦИИ:**\n"
        
        if not checks["has_title"]:
            report += "• Добавьте заголовок с # в начале файла\n"
        if not checks["has_installation"]:
            report += "• Добавьте раздел 'Installation' или 'Getting Started'\n"
        if not checks["has_usage"]:
            report += "• Добавьте примеры использования\n"
        if not checks["has_code_examples"]:
            report += "• Добавьте примеры кода в блоках ```\n"
        if not checks["has_links"]:
            report += "• Добавьте ссылки на документацию, issues и т.д.\n"
        
        report += f"\n📊 Статистика: {len(lines)} строк, {len(content.split())} слов"
        
        return report

