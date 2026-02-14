import base64
import os
import sys
from typing import Any, List, Optional, Dict
import httpx
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from mcp.server.fastmcp import FastMCP
import asyncio
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# Конфигурация
# -----------------------------
# GITLAB_URL = "http://localhost"  # Ваш локальный GitLab
GITLAB_URL = os.environ.get("GITLAB_URL")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")  # Токен с правами read_api, read_repository

# DevOps DORA метрики и аналитика
mcp = FastMCP("gitlab-devops-metrics", instructions="""
Этот инструмент предоставляет доступ к метрикам и аналитике DevOps из GitLab.
Доступные данные: DORA метрики, активность проекта, эффективность MR, пайплайны.
Используйте для мониторинга состояния проектов и анализа DevOps-экосистемы.
""")

HEADERS = {
    "PRIVATE-TOKEN": GITLAB_TOKEN,
    "Content-Type": "application/json"
}

# -----------------------------
# Утилиты
# -----------------------------
async def make_gitlab_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Выполнить запрос к GitLab API"""
    url = f"{GITLAB_URL}/api/v4{endpoint}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, headers=HEADERS, params=params or {})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Ошибка запроса к {url}: {e}")
            return None

async def get_project_info(project_id: str) -> Optional[Dict]:
    """Получить информацию о проекте"""
    return await make_gitlab_request(f"/projects/{project_id}")

# -----------------------------
# 1. DORA МЕТРИКИ (DevOps Research & Assessment)
# -----------------------------
@mcp.tool()
async def get_dora_metrics(project_id: str, start_date: str = None, end_date: str = None) -> str:
    """
    Получить DORA метрики для проекта: развертывание частоты, время выполнения, время восстановления
    
    Args:
        project_id: ID проекта в GitLab
        start_date: Начальная дата (YYYY-MM-DD), по умолчанию 30 дней назад
        end_date: Конечная дата (YYYY-MM-DD), по умолчанию сегодня
    """
    if not start_date:
        start_date = (datetime.now().replace(tzinfo=timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().replace(tzinfo=timezone.utc).strftime("%Y-%m-%d")
    
    # Получаем данные для расчета DORA метрик
    endpoints = [
        f"/projects/{project_id}/releases?per_page=100",
        f"/projects/{project_id}/deployments?updated_after={start_date}&updated_before={end_date}",
        f"/projects/{project_id}/merge_requests?state=merged&updated_after={start_date}&updated_before={end_date}",
        f"/projects/{project_id}/issues?state=closed&labels=incident&created_after={start_date}"
    ]
    
    results = await asyncio.gather(*[make_gitlab_request(e) for e in endpoints])
    
    releases = results[0] or []
    deployments = results[1] or []
    merge_requests = results[2] or []
    incidents = results[3] or []
    
    # Расчет метрик
    days_period = (datetime.strptime(end_date, "%Y-%m-%d") - 
                  datetime.strptime(start_date, "%Y-%m-%d")).days
    
    # Deployment Frequency (частота развертываний)
    deployment_freq = len(deployments) / max(days_period, 1)
    
    # Lead Time for Changes (время выполнения изменений)
    lead_times = []
    for mr in merge_requests[:50]:  # Берем последние 50 MR
        created_at = datetime.fromisoformat(mr['created_at'].replace('Z', '+00:00'))
        merged_at = datetime.fromisoformat(mr['merged_at'].replace('Z', '+00:00')) if mr.get('merged_at') else None
        if merged_at:
            lead_times.append((merged_at - created_at).days)
    
    avg_lead_time = sum(lead_times) / len(lead_times) if lead_times else 0
    
    # Time to Restore Service (время восстановления)
    restore_times = []
    for incident in incidents:
        created_at = datetime.fromisoformat(incident['created_at'].replace('Z', '+00:00'))
        closed_at = datetime.fromisoformat(incident['closed_at'].replace('Z', '+00:00')) if incident.get('closed_at') else None
        if closed_at:
            restore_times.append((closed_at - created_at).total_seconds() / 3600)  # в часах
    
    avg_restore_time = sum(restore_times) / len(restore_times) if restore_times else 0
    
    # Change Failure Rate (процент неудачных изменений)
    failed_deployments = len([d for d in deployments if d.get('status') == 'failed'])
    change_failure_rate = (failed_deployments / len(deployments)) * 100 if deployments else 0
    
    # Определение уровня DevOps по DORA
    devops_level = "Элитный"
    if deployment_freq < 1:
        devops_level = "Высокий" if avg_lead_time < 7 else "Средний"
    if change_failure_rate > 15:
        devops_level = "Низкий"
    
    result = f"""
📊 **DORA METRICS REPORT** — Проект {project_id}
Период: {start_date} — {end_date} ({days_period} дней)

**Ключевые метрики:**
• Deployment Frequency: {deployment_freq:.2f} развертываний/день
• Lead Time for Changes: {avg_lead_time:.1f} дней
• Time to Restore Service: {avg_restore_time:.1f} часов
• Change Failure Rate: {change_failure_rate:.1f}%

**Уровень DevOps: {devops_level}**

**Статистика:**
• Всего развертываний: {len(deployments)}
• Успешных: {len(deployments) - failed_deployments}
• Неудачных: {failed_deployments}
• Выпусков версий: {len(releases)}
• Объединенных MR: {len(merge_requests)}
• Инцидентов: {len(incidents)}
"""
    return result

# -----------------------------
# 2. АКТИВНОСТЬ ПРОЕКТА
# -----------------------------
@mcp.tool()
async def get_project_activity(project_id: str, days: int = 7) -> str:
    """
    Получить активность проекта за последние N дней
    
    Args:
        project_id: ID проекта
        days: Количество дней для анализа (по умолчанию 7)
    """
    end_date = datetime.now().replace(tzinfo=timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    # События проекта
    events = await make_gitlab_request(
        f"/projects/{project_id}/events",
        {"after": start_date.strftime("%Y-%m-%d"), "per_page": 100}
    ) or []
    
    # Группировка событий по типу
    event_counts = {}
    for event in events:
        event_type = event.get('action_name', 'unknown')
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    
    # Собираем статистику
    project = await get_project_info(project_id)
    project_name = project.get('name', project_id) if project else project_id
    
    result = f"""
📈 **ACTIVITY REPORT** — {project_name}
Период: {start_date.strftime('%Y-%m-%d')} — {end_date.strftime('%Y-%m-%d')} ({days} дней)

**Общая статистика:**
• Всего событий: {len(events)}
• Уникальных типов событий: {len(event_counts)}

**Распределение событий:**
"""
    
    for event_type, count in sorted(event_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(events)) * 100
        result += f"• {event_type}: {count} ({percentage:.1f}%)\n"
    
    # Последние важные события
    recent_important = [e for e in events if e['action_name'] in 
                       ['pushed to', 'merged', 'created', 'closed', 'commented on']][:5]
    
    if recent_important:
        result += "\n**Последние ключевые события:**\n"
        for event in recent_important:
            username = event.get('author', {}).get('username', 'unknown')
            action = event.get('action_name', 'unknown')
            result += f"• {username} — {action} ({event.get('created_at', '')[:10]})\n"
    
    return result

# -----------------------------
# 3. АНАЛИЗ MERGE REQUESTS
# -----------------------------
@mcp.tool()
async def analyze_merge_requests(project_id: str, timeframe: str = "month") -> str:
    """
    Проанализировать эффективность Merge Requests
    
    Args:
        project_id: ID проекта
        timeframe: Период анализа (week, month, quarter)
    """
    days_map = {"week": 7, "month": 30, "quarter": 90}
    days = days_map.get(timeframe, 30)
    
    start_date = (datetime.now().replace(tzinfo=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    
    # Получаем все MR за период
    mrs = await make_gitlab_request(
        f"/projects/{project_id}/merge_requests",
        {"created_after": start_date, "per_page": 100, "scope": "all"}
    ) or []
    
    if not mrs:
        return f"Нет Merge Requests за последние {days} дней в проекте {project_id}"
    
    # Анализ
    opened_mrs = [mr for mr in mrs if mr['state'] == 'opened']
    merged_mrs = [mr for mr in mrs if mr['state'] == 'merged']
    closed_mrs = [mr for mr in mrs if mr['state'] == 'closed']
    
    # Время на ревью
    review_times = []
    for mr in merged_mrs:
        if mr.get('created_at') and mr.get('merged_at'):
            created = datetime.fromisoformat(mr['created_at'].replace('Z', '+00:00'))
            merged = datetime.fromisoformat(mr['merged_at'].replace('Z', '+00:00'))
            review_times.append((merged - created).total_seconds() / 3600)  # в часах
    
    avg_review_time = sum(review_times) / len(review_times) if review_times else 0
    
    # Размеры MR (по изменениям)
    mr_sizes = []
    for mr in merged_mrs:
        if mr.get('changes_count'):
            mr_sizes.append(mr['changes_count'])
    
    avg_mr_size = sum(mr_sizes) / len(mr_sizes) if mr_sizes else 0
    
    # Авторы
    authors = {}
    for mr in mrs:
        author = mr.get('author', {}).get('username', 'unknown')
        authors[author] = authors.get(author, 0) + 1
    
    top_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)[:5]
    
    result = f"""
🔀 **MERGE REQUESTS ANALYSIS** — Проект {project_id}
Период: последние {days} дней ({timeframe})

**Общая статистика:**
• Всего MR: {len(mrs)}
• Открыто: {len(opened_mrs)}
• Объединено: {len(merged_mrs)} ({(len(merged_mrs)/len(mrs))*100:.1f}%)
• Закрыто: {len(closed_mrs)}

**Эффективность:**
• Среднее время ревью: {avg_review_time:.1f} часов
• Средний размер MR: {avg_mr_size:.0f} изменений
• Rate объединения: {(len(merged_mrs)/max(len(opened_mrs)+len(merged_mrs), 1))*100:.1f}%

**Топ авторов ({len(authors)} всего):**
"""
    
    for author, count in top_authors:
        result += f"• {author}: {count} MR\n"
    
    # Рекомендации
    result += "\n**Рекомендации:**\n"
    if avg_review_time > 48:
        result += "• ⚠️ Время ревью слишком длинное (>48ч)\n"
    if avg_mr_size > 500:
        result += "• ⚠️ MR слишком большие, разделяйте на меньшие\n"
    if len(opened_mrs) > 10:
        result += f"• ⚠️ Много открытых MR ({len(opened_mrs)}), нужен review day\n"
    
    return result

# -----------------------------
# 4. МОНИТОРИНГ ПАЙПЛАЙНОВ
# -----------------------------
@mcp.tool()
async def monitor_pipelines(project_id: str, limit: int = 20) -> str:
    """
    Мониторинг состояния CI/CD пайплайнов
    
    Args:
        project_id: ID проекта
        limit: Количество последних пайплайнов для анализа
    """
    pipelines = await make_gitlab_request(
        f"/projects/{project_id}/pipelines",
        {"per_page": limit, "order_by": "id", "sort": "desc"}
    ) or []
    
    if not pipelines:
        return f"Нет данных о пайплайнах в проекте {project_id}"
    
    # Статистика
    status_counts = {}
    duration_sum = 0
    successful = 0
    
    for pipeline in pipelines:
        status = pipeline.get('status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
        
        if pipeline.get('duration'):
            duration_sum += pipeline['duration']
        
        if status == 'success':
            successful += 1
    
    success_rate = (successful / len(pipelines)) * 100 if pipelines else 0
    avg_duration = duration_sum / len(pipelines) if pipelines else 0
    
    # Последние пайплайны
    recent_pipelines = pipelines[:5]
    
    result = f"""
⚙️ **PIPELINE MONITORING** — Проект {project_id}
Анализ последних {len(pipelines)} пайплайнов

**Статистика:**
• Success Rate: {success_rate:.1f}%
• Средняя длительность: {avg_duration:.0f} секунд
• Всего пайплайнов: {len(pipelines)}

**Распределение по статусам:**
"""
    
    for status, count in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(pipelines)) * 100
        result += f"• {status}: {count} ({percentage:.1f}%)\n"
    
    result += "\n**Последние пайплайны:**\n"
    for pipe in recent_pipelines:
        status_icon = "✅" if pipe['status'] == 'success' else "❌" if pipe['status'] == 'failed' else "⏳"
        result += f"• #{pipe['id']} {status_icon} {pipe['status']} ({pipe.get('duration', 0)}s) — {pipe['ref']}\n"
    
    # Предупреждения
    result += "\n**Предупреждения:**\n"
    if success_rate < 80:
        result += f"• ⚠️ Низкий success rate ({success_rate:.1f}%)\n"
    if 'failed' in status_counts and status_counts['failed'] > 3:
        result += f"• ⚠️ Много неудачных пайплайнов ({status_counts['failed']})\n"
    
    return result

# -----------------------------
# 5. АНАЛИЗ ИНЦИДЕНТОВ И БАГОВ
# -----------------------------
@mcp.tool()
async def analyze_issues(project_id: str, label: str = None) -> str:
    """
    Анализ issues (багов, инцидентов, задач)
    
    Args:
        project_id: ID проекта
        label: Фильтр по метке (например, bug, incident, enhancement)
    """
    params = {"per_page": 100, "scope": "all"}
    if label:
        params["labels"] = label
    
    issues = await make_gitlab_request(f"/projects/{project_id}/issues", params) or []
    
    if not issues:
        return f"Нет issues{' с меткой ' + label if label else ''} в проекте {project_id}"
    
    # Статистика
    state_counts = {}
    label_counts = {}
    assignee_counts = {}
    
    for issue in issues:
        state = issue.get('state', 'opened')
        state_counts[state] = state_counts.get(state, 0) + 1
        
        # Метки
        for lbl in issue.get('labels', []):
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
        
        # Назначенные
        if issue.get('assignee'):
            assignee = issue.get('assignee', {}).get('username', 'unknown')
            assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1
    
    # Время открытия issues
    now = datetime.now().replace(tzinfo=timezone.utc)
    age_groups = {"<1 день": 0, "1-7 дней": 0, "1-4 недели": 0, ">1 месяца": 0}
    
    for issue in issues:
        if issue['state'] == 'opened':
            created = datetime.fromisoformat(issue['created_at'].replace('Z', '+00:00'))
            age = (now - created).days
            
            if age < 1:
                age_groups["<1 день"] += 1
            elif age <= 7:
                age_groups["1-7 дней"] += 1
            elif age <= 30:
                age_groups["1-4 недели"] += 1
            else:
                age_groups[">1 месяца"] += 1
    
    result = f"""
🐛 **ISSUES ANALYSIS** — Проект {project_id}
{'Фильтр: ' + label if label else 'Все issues'}

**Общая статистика:**
• Всего issues: {len(issues)}
• Открыто: {state_counts.get('opened', 0)}
• Закрыто: {state_counts.get('closed', 0)}
• Заблокировано: {len([i for i in issues if i.get('discussion_locked')])}

**Возраст открытых issues:**
"""
    
    for group, count in age_groups.items():
        if count > 0:
            result += f"• {group}: {count}\n"
    
    # Топ меток
    if label_counts:
        result += "\n**Топ меток:**\n"
        for lbl, count in sorted(label_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            result += f"• {lbl}: {count}\n"
    
    # Назначенные
    if assignee_counts:
        result += "\n**Распределение по исполнителям:**\n"
        for assignee, count in sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            result += f"• {assignee}: {count} issues\n"
    
    # Критические issues
    critical_issues = [i for i in issues 
                      if i['state'] == 'opened' and 
                      ('critical' in i.get('labels', []) or 
                       'severity::1' in i.get('labels', []))]
    
    if critical_issues:
        result += f"\n⚠️ **Критические issues ({len(critical_issues)}):**\n"
        for issue in critical_issues[:3]:
            result += f"• #{issue['iid']}: {issue['title']} (создано: {issue['created_at'][:10]})\n"
    
    return result

# -----------------------------
# 6. ОБЗОР ВСЕХ ПРОЕКТОВ (ГРУППЫ)
# -----------------------------
@mcp.tool()
async def list_group_projects(group_id: str, with_metrics: bool = False) -> str:
    """
    Показать все проекты в группе с основной статистикой
    
    Args:
        group_id: ID группы в GitLab
        with_metrics: Включить базовые метрики для каждого проекта
    """
    projects = await make_gitlab_request(f"/groups/{group_id}/projects", {"per_page": 50}) or []
    
    if not projects:
        return f"Нет проектов в группе {group_id} или группа не найдена"
    
    result = f"""
🏢 **GROUP PROJECTS OVERVIEW** — Группа {group_id}
Всего проектов: {len(projects)}

"""
    
    for i, project in enumerate(projects, 1):
        result += f"{i}. **{project['name']}** (ID: {project['id']})\n"
        result += f"   • Путь: {project['path_with_namespace']}\n"
        result += f"   • Звезд: {project.get('star_count', 0)} | Форков: {project.get('forks_count', 0)}\n"
        result += f"   • Последняя активность: {project.get('last_activity_at', 'N/A')[:10]}\n"
        
        if with_metrics:
            # Базовые метрики
            project_id = project['id']
            
            # Асинхронные запросы для метрик
            endpoints = [
                f"/projects/{project_id}/merge_requests?state=opened&per_page=1",
                f"/projects/{project_id}/issues?state=opened&per_page=1",
                f"/projects/{project_id}/pipelines?per_page=1"
            ]
            
            metrics_data = await asyncio.gather(*[make_gitlab_request(e) for e in endpoints])
            
            open_mrs = len(metrics_data[0]) if metrics_data[0] else 0
            open_issues = len(metrics_data[1]) if metrics_data[1] else 0
            has_pipelines = bool(metrics_data[2])
            
            result += f"   • Открыто MR: {open_mrs} | Issues: {open_issues}\n"
            result += f"   • CI/CD: {'✅' if has_pipelines else '❌'}\n"
        
        result += "\n"
    
    return result

# -----------------------------
# 7. КОМПЛЕКСНЫЙ ОТЧЕТ ПО ПРОЕКТУ
# -----------------------------
@mcp.tool()
async def project_health_report(project_id: str) -> str:
    """
    Полный отчет о здоровье проекта
    
    Args:
        project_id: ID проекта
    """
    # Собираем все данные параллельно
    endpoints = [
        f"/projects/{project_id}",
        f"/projects/{project_id}/merge_requests?state=opened&per_page=10",
        f"/projects/{project_id}/issues?state=opened&per_page=10",
        f"/projects/{project_id}/pipelines?per_page=5",
        f"/projects/{project_id}/events?per_page=20"
    ]
    
    results = await asyncio.gather(*[make_gitlab_request(e) for e in endpoints])
    
    project_info = results[0]
    open_mrs = results[1] or []
    open_issues = results[2] or []
    pipelines = results[3] or []
    recent_events = results[4] or []
    
    if not project_info:
        return f"Проект {project_id} не найден"
    
    # Анализ
    project_name = project_info.get('name', project_id)
    
    # Статус пайплайнов
    pipeline_status = "unknown"
    if pipelines:
        latest = pipelines[0]
        pipeline_status = f"{latest.get('status', 'unknown')} (#{latest.get('id')})"
    
    # Активность
    activity_days = 0
    if project_info.get('last_activity_at'):
        last_activity = datetime.fromisoformat(
            project_info['last_activity_at'].replace('Z', '+00:00')
        )
        activity_days = (datetime.now().replace(tzinfo=timezone.utc) - last_activity).days
    
    result = f"""
🏥 **PROJECT HEALTH REPORT** — {project_name}
ID: {project_id} | Путь: {project_info.get('path_with_namespace', 'N/A')}

**ОБЩИЙ СТАТУС:**
• Последняя активность: {activity_days} дней назад
• Видимость: {project_info.get('visibility', 'N/A')}
• Статус последнего пайплайна: {pipeline_status}
• Звезд: {project_info.get('star_count', 0)} | Форков: {project_info.get('forks_count', 0)}

**ТЕКУЩАЯ НАГРУЗКА:**
• Открыто Merge Requests: {len(open_mrs)}
• Открыто Issues: {len(open_issues)}
• Активных веток: {project_info.get('repository', {}).get('branch_count', 'N/A')}

**ПОСЛЕДНИЕ СОБЫТИЯ ({len(recent_events)}):**
"""
    
    for event in recent_events[:5]:
        user = event.get('author', {}).get('username', 'unknown')
        action = event.get('action_name', 'unknown')
        result += f"• {user} — {action} ({event.get('created_at', '')[:16]})\n"
    
    # Оценка здоровья
    health_score = 100
    
    if activity_days > 30:
        health_score -= 30
        result += f"\n⚠️  Нет активности более 30 дней (-30 баллов)\n"
    
    if len(open_mrs) > 15:
        health_score -= 20
        result += f"⚠️  Слишком много открытых MR (>15) (-20 баллов)\n"
    
    if len(open_issues) > 50:
        health_score -= 25
        result += f"⚠️  Слишком много открытых issues (>50) (-25 баллов)\n"
    
    if pipelines and pipelines[0].get('status') == 'failed':
        health_score -= 15
        result += f"⚠️  Последний пайплайн неудачный (-15 баллов)\n"
    
    # Итоговая оценка
    health_status = "✅ Отличное" if health_score >= 80 else "⚠️ Требует внимания" if health_score >= 60 else "❌ Критическое"
    
    result += f"""
**ИТОГОВАЯ ОЦЕНКА ЗДОРОВЬЯ ПРОЕКТА:**
• Баллы: {health_score}/100
• Статус: {health_status}

{'✅ Проект в хорошем состоянии' if health_score >= 80 else 
 '⚠️ Есть проблемы, требующие внимания' if health_score >= 60 else 
 '❌ Критическое состояние, требуется срочное вмешательство'}
"""
    
    return result

# -----------------------------
# Запуск сервера
# -----------------------------




if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


# from readme import register_readme_tools


async def get_project_readme_content(project_id: str, ref: str = "main") -> Optional[Dict[str, Any]]:
    """
    Получить содержимое README файла проекта
    
    Args:
        project_id: ID или путь к проекту (например: 'namespace/project' или 123)
        ref: ветка/тег (по умолчанию: main)
    
    Returns:
        Словарь с содержимым README или None при ошибке
    """
    
    # Сначала ищем README файлы в корне проекта
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Получаем список файлов в корне репозитория
        tree_response = await client.get(
            f"{GITLAB_URL}/api/v4/projects/{quote(str(project_id), safe='')}/repository/tree",
            headers=HEADERS,
            params={"ref": ref, "path": "", "per_page": 100}
        )
        
        print(tree_response)
        
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
            f"{GITLAB_URL}/api/v4/projects/{quote(str(project_id), safe='')}/repository/files/{quote(readme_file['path'])}",
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
            
    # except httpx.HTTPStatusError as e:
    #     return {
    #         "status": "error",
    #         "message": f"HTTP ошибка: {e.response.status_code}",
    #         "details": str(e)
    #     }
    # except Exception as e:
    #     return {
    #         "status": "error",
    #         "message": f"Ошибка при получении README: {str(e)}"
    #     }

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

if __name__ == "__main__":
    print("🚀 Запуск GitLab DevOps Metrics MCP сервера...")
    print(f"• GitLab URL: {GITLAB_URL}")
    print(f"• Доступно инструментов: 7")
    print("• Ожидание подключения AI-клиента...")
    # register_readme_tools(mcp)
    
    mcp.run(transport="stdio")