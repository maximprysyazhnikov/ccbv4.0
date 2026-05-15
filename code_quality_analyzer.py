#!/usr/bin/env python3
"""
PHASE 4: CODE QUALITY REVIEW
Аналіз якості коду, архітектури та performance
"""
import os
import ast
import re
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CodeQualityAnalyzer:
    """Аналізатор якості коду"""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.results = {}

    def analyze_project_structure(self) -> Dict[str, Any]:
        """Аналіз структури проекту"""
        logger.info("🔍 Аналіз структури проекту...")

        structure = {
            'total_files': 0,
            'python_files': 0,
            'directories': 0,
            'file_sizes': {},
            'largest_files': [],
            'empty_files': []
        }

        for root, dirs, files in os.walk(self.project_root):
            # Пропускаємо директорії які не потрібно аналізувати
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', '.git']]

            structure['directories'] += len(dirs)

            for file in files:
                if file.startswith('.') or file.endswith('.pyc'):
                    continue

                structure['total_files'] += 1
                filepath = Path(root) / file

                if file.endswith('.py'):
                    structure['python_files'] += 1

                try:
                    size = filepath.stat().st_size
                    structure['file_sizes'][str(filepath.relative_to(self.project_root))] = size

                    if size > 1024 * 1024:  # > 1MB
                        structure['largest_files'].append((str(filepath.relative_to(self.project_root)), size))

                    if size == 0:
                        structure['empty_files'].append(str(filepath.relative_to(self.project_root)))

                except OSError:
                    continue

        # Сортуємо за розміром
        structure['largest_files'].sort(key=lambda x: x[1], reverse=True)
        structure['largest_files'] = structure['largest_files'][:10]  # Топ 10

        self.results['project_structure'] = structure
        return structure

    def analyze_code_quality(self) -> Dict[str, Any]:
        """Аналіз якості Python коду"""
        logger.info("🔍 Аналіз якості Python коду...")

        quality_metrics = {
            'files_analyzed': 0,
            'total_lines': 0,
            'total_functions': 0,
            'total_classes': 0,
            'complexity_warnings': [],
            'code_smells': [],
            'imports_analysis': {},
            'async_usage': 0
        }

        python_files = list(self.project_root.rglob('*.py'))

        for py_file in python_files:
            if '__pycache__' in str(py_file) or py_file.name.startswith('.'):
                continue

            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                quality_metrics['files_analyzed'] += 1
                lines = content.split('\n')
                quality_metrics['total_lines'] += len(lines)

                # Аналіз AST
                try:
                    tree = ast.parse(content, filename=str(py_file))

                    for node in ast.walk(tree):
                        if isinstance(node, ast.FunctionDef):
                            quality_metrics['total_functions'] += 1

                            # Перевірка складності функції (проста евристика)
                            if len(lines[node.lineno-1:node.end_lineno]) > 50:
                                quality_metrics['complexity_warnings'].append({
                                    'file': str(py_file.relative_to(self.project_root)),
                                    'function': node.name,
                                    'lines': node.end_lineno - node.lineno + 1
                                })

                        elif isinstance(node, ast.ClassDef):
                            quality_metrics['total_classes'] += 1

                        elif isinstance(node, ast.AsyncFunctionDef):
                            quality_metrics['async_usage'] += 1

                except SyntaxError as e:
                    quality_metrics['code_smells'].append({
                        'file': str(py_file.relative_to(self.project_root)),
                        'type': 'syntax_error',
                        'message': str(e)
                    })

                # Аналіз імпортів
                imports = re.findall(r'^(?:from\s+\w+(?:\.\w+)*\s+import|import\s+\w+)', content, re.MULTILINE)
                quality_metrics['imports_analysis'][str(py_file.relative_to(self.project_root))] = len(imports)

            except Exception as e:
                logger.error(f"Помилка аналізу {py_file}: {e}")
                continue

        self.results['code_quality'] = quality_metrics
        return quality_metrics

    def analyze_architecture(self) -> Dict[str, Any]:
        """Аналіз архітектури проекту"""
        logger.info("🔍 Аналіз архітектури проекту...")

        architecture = {
            'main_entry_points': [],
            'service_layers': [],
            'data_layers': [],
            'ui_layers': [],
            'dependencies': {},
            'circular_imports': [],
            'solid_violations': []
        }

        # Аналіз основних entry points
        main_files = ['main.py', 'app.py', 'application.py', 'run.py']
        for main_file in main_files:
            if (self.project_root / main_file).exists():
                architecture['main_entry_points'].append(main_file)

        # Аналіз директорій
        dirs = [d for d in self.project_root.iterdir() if d.is_dir() and not d.name.startswith('.')]

        for dir_path in dirs:
            dir_name = dir_path.name

            if dir_name in ['services', 'core', 'business_logic']:
                architecture['service_layers'].append(dir_name)
            elif dir_name in ['models', 'storage', 'database', 'data']:
                architecture['data_layers'].append(dir_name)
            elif dir_name in ['telegram_bot', 'web', 'ui', 'handlers']:
                architecture['ui_layers'].append(dir_name)

        # Аналіз залежностей (простий)
        try:
            with open(self.project_root / 'requirements.txt', 'r') as f:
                deps = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                architecture['dependencies']['requirements_count'] = len(deps)
                architecture['dependencies']['major_deps'] = deps[:10]
        except FileNotFoundError:
            architecture['dependencies']['requirements_count'] = 0

        self.results['architecture'] = architecture
        return architecture

    def analyze_performance(self) -> Dict[str, Any]:
        """Аналіз performance аспектів"""
        logger.info("🔍 Аналіз performance...")

        performance = {
            'database_queries': [],
            'memory_usage': {},
            'async_patterns': 0,
            'caching_usage': 0,
            'bottlenecks': []
        }

        # Пошук SQL запитів
        python_files = list(self.project_root.rglob('*.py'))

        for py_file in python_files:
            if '__pycache__' in str(py_file):
                continue

            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                # Пошук SQL запитів
                sql_patterns = [
                    r'execute\s*\(\s*[\'\"](SELECT|INSERT|UPDATE|DELETE)',
                    r'conn\.execute\s*\(',
                    r'cursor\.execute\s*\('
                ]

                for pattern in sql_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    if matches:
                        performance['database_queries'].append({
                            'file': str(py_file.relative_to(self.project_root)),
                            'query_count': len(matches)
                        })

                # Пошук async/await патернів
                async_count = len(re.findall(r'\basync\s+def\b', content))
                await_count = len(re.findall(r'\bawait\s+', content))
                performance['async_patterns'] += async_count + await_count

                # Пошук кешування
                cache_patterns = [r'@cache', r'@lru_cache', r'memcache', r'redis']
                for pattern in cache_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        performance['caching_usage'] += 1
                        break

            except Exception as e:
                logger.error(f"Помилка аналізу performance для {py_file}: {e}")
                continue

        self.results['performance'] = performance
        return performance

    def analyze_security(self) -> Dict[str, Any]:
        """Базовий аналіз безпеки"""
        logger.info("🔍 Аналіз безпеки...")

        security = {
            'sql_injection_risks': [],
            'hardcoded_secrets': [],
            'input_validation': [],
            'error_handling': []
        }

        python_files = list(self.project_root.rglob('*.py'))

        for py_file in python_files:
            if '__pycache__' in str(py_file):
                continue

            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                lines = content.split('\n')

                for i, line in enumerate(lines, 1):
                    # Пошук потенційних SQL ін'єкцій
                    if 'execute(' in line and ('%' in line or '+' in line):
                        if 'cursor.execute' in line or 'conn.execute' in line:
                            security['sql_injection_risks'].append({
                                'file': str(py_file.relative_to(self.project_root)),
                                'line': i,
                                'code': line.strip()
                            })

                    # Пошук hardcoded секретів
                    secret_patterns = [
                        r'password\s*=\s*[\'"][^\'"]+[\'"]',
                        r'secret\s*=\s*[\'"][^\'"]+[\'"]',
                        r'key\s*=\s*[\'"][^\'"]+[\'"]',
                        r'token\s*=\s*[\'"][^\'"]+[\'"]'
                    ]

                    for pattern in secret_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            security['hardcoded_secrets'].append({
                                'file': str(py_file.relative_to(self.project_root)),
                                'line': i,
                                'type': 'potential_secret'
                            })

                    # Перевірка обробки помилок
                    if 'except:' in line and 'Exception' not in line:
                        security['error_handling'].append({
                            'file': str(py_file.relative_to(self.project_root)),
                            'line': i,
                            'issue': 'bare_except'
                        })

            except Exception as e:
                logger.error(f"Помилка аналізу безпеки для {py_file}: {e}")
                continue

        self.results['security'] = security
        return security

    def generate_report(self) -> str:
        """Генерація звіту"""
        report = []
        report.append("# 📊 PHASE 4: CODE QUALITY REVIEW REPORT")
        report.append(f"**Timestamp:** {datetime.now().isoformat()}")
        report.append("")

        # Структура проекту
        if 'project_structure' in self.results:
            struct = self.results['project_structure']
            report.append("## 📁 PROJECT STRUCTURE")
            report.append(f"- **Total files:** {struct['total_files']}")
            report.append(f"- **Python files:** {struct['python_files']}")
            report.append(f"- **Directories:** {struct['directories']}")
            report.append(f"- **Largest files:** {len(struct['largest_files'])}")
            report.append(f"- **Empty files:** {len(struct['empty_files'])}")
            report.append("")

        # Якість коду
        if 'code_quality' in self.results:
            quality = self.results['code_quality']
            report.append("## 🔍 CODE QUALITY")
            report.append(f"- **Files analyzed:** {quality['files_analyzed']}")
            report.append(f"- **Total lines:** {quality['total_lines']}")
            report.append(f"- **Functions:** {quality['total_functions']}")
            report.append(f"- **Classes:** {quality['total_classes']}")
            report.append(f"- **Async functions:** {quality['async_usage']}")
            report.append(f"- **Complexity warnings:** {len(quality['complexity_warnings'])}")
            report.append(f"- **Code smells:** {len(quality['code_smells'])}")
            report.append("")

        # Архітектура
        if 'architecture' in self.results:
            arch = self.results['architecture']
            report.append("## 🏗️ ARCHITECTURE")
            report.append(f"- **Entry points:** {', '.join(arch['main_entry_points'])}")
            report.append(f"- **Service layers:** {', '.join(arch['service_layers'])}")
            report.append(f"- **Data layers:** {', '.join(arch['data_layers'])}")
            report.append(f"- **UI layers:** {', '.join(arch['ui_layers'])}")
            report.append(f"- **Dependencies:** {arch['dependencies'].get('requirements_count', 0)}")
            report.append("")

        # Performance
        if 'performance' in self.results:
            perf = self.results['performance']
            report.append("## ⚡ PERFORMANCE")
            report.append(f"- **Database queries:** {len(perf['database_queries'])} files")
            report.append(f"- **Async patterns:** {perf['async_patterns']}")
            report.append(f"- **Caching usage:** {perf['caching_usage']}")
            report.append("")

        # Безпека
        if 'security' in self.results:
            sec = self.results['security']
            report.append("## 🔒 SECURITY")
            report.append(f"- **SQL injection risks:** {len(sec['sql_injection_risks'])}")
            report.append(f"- **Hardcoded secrets:** {len(sec['hardcoded_secrets'])}")
            report.append(f"- **Error handling issues:** {len(sec['error_handling'])}")
            report.append("")

        # Рекомендації
        report.append("## 💡 RECOMMENDATIONS")
        recommendations = self._generate_recommendations()
        for rec in recommendations:
            report.append(f"- {rec}")
        report.append("")

        return "\n".join(report)

    def _generate_recommendations(self) -> List[str]:
        """Генерація рекомендацій на основі аналізу"""
        recommendations = []

        # Структура
        if 'project_structure' in self.results:
            struct = self.results['project_structure']
            if len(struct['empty_files']) > 0:
                recommendations.append(f"Видалити {len(struct['empty_files'])} порожніх файлів")
            if len(struct['largest_files']) > 0:
                recommendations.append("Розглянути рефакторинг великих файлів (>1MB)")

        # Якість коду
        if 'code_quality' in self.results:
            quality = self.results['code_quality']
            if len(quality['complexity_warnings']) > 0:
                recommendations.append(f"Рефакторити {len(quality['complexity_warnings'])} складних функцій (>50 рядків)")
            if len(quality['code_smells']) > 0:
                recommendations.append(f"Виправити {len(quality['code_smells'])} code smells")

        # Архітектура
        if 'architecture' in self.results:
            arch = self.results['architecture']
            if not arch['main_entry_points']:
                recommendations.append("Додати чіткий main entry point")
            if len(arch['service_layers']) == 0:
                recommendations.append("Впровадити service layer паттерн")

        # Performance
        if 'performance' in self.results:
            perf = self.results['performance']
            if len(perf['database_queries']) > 10:
                recommendations.append("Оптимізувати database queries (можливо додати індекси)")
            if perf['async_patterns'] < perf.get('total_functions', 0) * 0.1:
                recommendations.append("Розглянути використання async/await для I/O операцій")

        # Безпека
        if 'security' in self.results:
            sec = self.results['security']
            if len(sec['sql_injection_risks']) > 0:
                recommendations.append(f"Перевірити {len(sec['sql_injection_risks'])} потенційних SQL ін'єкцій")
            if len(sec['hardcoded_secrets']) > 0:
                recommendations.append(f"Перемістити {len(sec['hardcoded_secrets'])} hardcoded секретів в env змінні")

        return recommendations

    def run_analysis(self) -> Dict[str, Any]:
        """Запуск повного аналізу"""
        logger.info("🚀 ЗАПУСК ФАЗИ 4: CODE QUALITY REVIEW")

        results = {
            'timestamp': datetime.now().isoformat(),
            'phase': 'code_quality_review',
            'status': 'running'
        }

        try:
            # 1. Структура проекту
            structure = self.analyze_project_structure()

            # 2. Якість коду
            quality = self.analyze_code_quality()

            # 3. Архітектура
            architecture = self.analyze_architecture()

            # 4. Performance
            performance = self.analyze_performance()

            # 5. Безпека
            security = self.analyze_security()

            # 6. Генерація звіту
            report = self.generate_report()

            results.update({
                'project_structure': structure,
                'code_quality': quality,
                'architecture': architecture,
                'performance': performance,
                'security': security,
                'report': report,
                'status': 'completed'
            })

            logger.info("✅ Фаза 4 завершена успішно")

        except Exception as e:
            logger.error(f"❌ Помилка аналізу: {e}")
            results['status'] = 'failed'
            results['error'] = str(e)

        # Збереження результатів
        self.save_results(results)

        return results

    def save_results(self, results: Dict[str, Any]) -> None:
        """Збереження результатів в файл"""
        output_file = Path('phase4_code_quality.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Результати збережено в {output_file}")

        # Також зберегти текстовий звіт
        report_file = Path('phase4_code_report.md')
        if 'report' in results:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(results['report'])

            logger.info(f"Звіт збережено в {report_file}")


def main():
    """Основна функція"""
    analyzer = CodeQualityAnalyzer()
    results = analyzer.run_analysis()

    # Вивід основних результатів
    print("\n" + "="*60)
    print("PHASE 4: CODE QUALITY REVIEW RESULTS")
    print("="*60)

    if results['status'] == 'completed':
        print("✅ Analysis completed successfully")

        if 'code_quality' in results:
            quality = results['code_quality']
            print(f"📊 Files analyzed: {quality['files_analyzed']}")
            print(f"📝 Total lines: {quality['total_lines']}")
            print(f"🔧 Functions: {quality['total_functions']}")
            print(f"🏗️ Classes: {quality['total_classes']}")

        if 'security' in results:
            sec = results['security']
            print(f"🔒 Security issues: {len(sec['sql_injection_risks']) + len(sec['hardcoded_secrets'])}")

    else:
        print(f"❌ Analysis failed: {results.get('error', 'Unknown error')}")

    print("\n📄 Detailed results saved to phase4_code_quality.json")
    print("📄 Report saved to phase4_code_report.md")


if __name__ == "__main__":
    main()