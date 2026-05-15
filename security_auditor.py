#!/usr/bin/env python3
"""
PHASE 5: БЕЗПЕКА ТА КОНФІГУРАЦІЯ
Аудит безпеки та перевірка конфігурації
"""
import os
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

class SecurityConfigAuditor:
    """Аудитор безпеки та конфігурації"""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.results = {}

    def audit_secrets_and_credentials(self) -> Dict[str, Any]:
        """Аудит секретів та credentials"""
        logger.info("🔐 Аудит секретів та credentials...")

        secrets_audit = {
            'hardcoded_secrets': [],
            'env_variables': [],
            'config_files': [],
            'api_keys': [],
            'database_credentials': [],
            'missing_env_vars': []
        }

        # Перевірка .env файлів
        env_files = ['.env', '.env.local', '.env.production', '.env.development']
        for env_file in env_files:
            env_path = self.project_root / env_file
            if env_path.exists():
                secrets_audit['config_files'].append(str(env_path.relative_to(self.project_root)))

        # Пошук hardcoded секретів в коді
        python_files = list(self.project_root.rglob('*.py'))

        secret_patterns = [
            (r'password\s*=\s*[\'"][^\'"]+[\'"]', 'password'),
            (r'secret\s*=\s*[\'"][^\'"]+[\'"]', 'secret'),
            (r'key\s*=\s*[\'"][^\'"]+[\'"]', 'key'),
            (r'token\s*=\s*[\'"][^\'"]+[\'"]', 'token'),
            (r'api_key\s*=\s*[\'"][^\'"]+[\'"]', 'api_key'),
            (r'db_password\s*=\s*[\'"][^\'"]+[\'"]', 'db_password'),
            (r'connection_string\s*=\s*[\'"][^\'"]+[\'"]', 'connection_string'),
        ]

        for py_file in python_files:
            if '__pycache__' in str(py_file):
                continue

            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    lines = content.split('\n')

                for i, line in enumerate(lines, 1):
                    for pattern, secret_type in secret_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            # Перевірка чи це не коментар або не env змінна
                            if not line.strip().startswith('#') and 'os.getenv' not in line and 'getenv' not in line:
                                secrets_audit['hardcoded_secrets'].append({
                                    'file': str(py_file.relative_to(self.project_root)),
                                    'line': i,
                                    'type': secret_type,
                                    'code': line.strip()
                                })

            except Exception as e:
                logger.error(f"Помилка аналізу {py_file}: {e}")
                continue

        # Перевірка env змінних що використовуються
        env_vars_used = set()
        for py_file in python_files:
            if '__pycache__' in str(py_file):
                continue

            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                # Пошук os.getenv викликів
                getenv_matches = re.findall(r'os\.getenv\s*\(\s*[\'"]([^\'"]+)[\'"]', content)
                environ_matches = re.findall(r'os\.environ\s*\[\s*[\'"]([^\'"]+)[\'"]\]', content)
                environ_get_matches = re.findall(r'os\.environ\.get\s*\(\s*[\'"]([^\'"]+)[\'"]', content)

                env_vars_used.update(getenv_matches + environ_matches + environ_get_matches)

            except Exception as e:
                continue

        secrets_audit['env_variables'] = list(env_vars_used)

        self.results['secrets_audit'] = secrets_audit
        return secrets_audit

    def audit_input_validation(self) -> Dict[str, Any]:
        """Аудит валідації input"""
        logger.info("🔍 Аудит валідації input...")

        validation_audit = {
            'missing_validation': [],
            'sql_injection_risks': [],
            'xss_risks': [],
            'command_injection_risks': []
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
                    # SQL injection перевірка
                    if ('execute(' in line or 'cursor.execute' in line) and ('%' in line or '+' in line or 'format(' in line):
                        if 'cursor.execute' in line or 'conn.execute' in line:
                            validation_audit['sql_injection_risks'].append({
                                'file': str(py_file.relative_to(self.project_root)),
                                'line': i,
                                'code': line.strip(),
                                'risk': 'potential_sql_injection'
                            })

                    # Command injection перевірка
                    if ('subprocess.' in line or 'os.system' in line or 'os.popen' in line) and ('+' in line or 'format(' in line):
                        validation_audit['command_injection_risks'].append({
                            'file': str(py_file.relative_to(self.project_root)),
                            'line': i,
                            'code': line.strip(),
                            'risk': 'potential_command_injection'
                        })

                    # XSS в web контексті (якщо є)
                    if ('telegram' in content.lower() or 'bot' in content.lower()) and ('<' in line and '>' in line):
                        validation_audit['xss_risks'].append({
                            'file': str(py_file.relative_to(self.project_root)),
                            'line': i,
                            'code': line.strip(),
                            'risk': 'potential_xss_in_telegram'
                        })

            except Exception as e:
                logger.error(f"Помилка аналізу {py_file}: {e}")
                continue

        self.results['input_validation'] = validation_audit
        return validation_audit

    def audit_error_handling(self) -> Dict[str, Any]:
        """Аудит обробки помилок"""
        logger.info("🚨 Аудит обробки помилок...")

        error_audit = {
            'bare_except_blocks': [],
            'missing_exception_types': [],
            'unhandled_exceptions': [],
            'logging_issues': []
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
                    # Bare except blocks
                    if line.strip() == 'except:' or line.strip().startswith('except:'):
                        error_audit['bare_except_blocks'].append({
                            'file': str(py_file.relative_to(self.project_root)),
                            'line': i,
                            'code': line.strip()
                        })

                    # Missing exception types
                    if 'except Exception' in line and ' as ' not in line:
                        error_audit['missing_exception_types'].append({
                            'file': str(py_file.relative_to(self.project_root)),
                            'line': i,
                            'code': line.strip()
                        })

            except Exception as e:
                logger.error(f"Помилка аналізу {py_file}: {e}")
                continue

        self.results['error_handling'] = error_audit
        return error_audit

    def audit_configuration_management(self) -> Dict[str, Any]:
        """Аудит управління конфігурацією"""
        logger.info("⚙️ Аудит управління конфігурацією...")

        config_audit = {
            'config_files': [],
            'settings_files': [],
            'env_files': [],
            'hardcoded_values': [],
            'missing_defaults': []
        }

        # Пошук конфігураційних файлів
        config_patterns = ['*.json', '*.yaml', '*.yml', '*.toml', '*.ini', '*.cfg']
        for pattern in config_patterns:
            for config_file in self.project_root.rglob(pattern):
                if not config_file.name.startswith('.'):
                    config_audit['config_files'].append(str(config_file.relative_to(self.project_root)))

        # Пошук settings файлів
        settings_files = list(self.project_root.rglob('*settings*.py'))
        config_audit['settings_files'] = [str(f.relative_to(self.project_root)) for f in settings_files]

        # Пошук .env файлів
        env_files = ['.env', '.env.local', '.env.production', '.env.development']
        for env_file in env_files:
            if (self.project_root / env_file).exists():
                config_audit['env_files'].append(env_file)

        # Пошук hardcoded значень в settings
        for settings_file in settings_files:
            try:
                with open(settings_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    lines = content.split('\n')

                for i, line in enumerate(lines, 1):
                    # Пошук hardcoded URL/API endpoints
                    if re.search(r'https?://[^\s\'"]+', line):
                        config_audit['hardcoded_values'].append({
                            'file': str(settings_file.relative_to(self.project_root)),
                            'line': i,
                            'type': 'hardcoded_url',
                            'code': line.strip()
                        })

                    # Пошук hardcoded ports
                    if re.search(r'port\s*=\s*\d+', line, re.IGNORECASE):
                        config_audit['hardcoded_values'].append({
                            'file': str(settings_file.relative_to(self.project_root)),
                            'line': i,
                            'type': 'hardcoded_port',
                            'code': line.strip()
                        })

            except Exception as e:
                logger.error(f"Помилка аналізу {settings_file}: {e}")
                continue

        self.results['configuration'] = config_audit
        return config_audit

    def audit_dependencies(self) -> Dict[str, Any]:
        """Аудит залежностей"""
        logger.info("📦 Аудит залежностей...")

        deps_audit = {
            'requirements_files': [],
            'outdated_packages': [],
            'security_vulnerabilities': [],
            'unused_imports': []
        }

        # Перевірка requirements файлів
        req_files = ['requirements.txt', 'requirements-dev.txt', 'pyproject.toml', 'setup.py']
        for req_file in req_files:
            if (self.project_root / req_file).exists():
                deps_audit['requirements_files'].append(req_file)

        # Читання requirements.txt
        req_path = self.project_root / 'requirements.txt'
        if req_path.exists():
            try:
                with open(req_path, 'r', encoding='utf-8') as f:
                    deps = [line.strip() for line in f if line.strip() and not line.startswith('#')]

                # Перевірка на відомі вразливості (простий чек)
                vulnerable_packages = ['django<3.2', 'flask<2.0', 'requests<2.25']
                for dep in deps:
                    for vuln in vulnerable_packages:
                        if vuln.split('<')[0] in dep and '<' in dep:
                            deps_audit['security_vulnerabilities'].append({
                                'package': dep,
                                'vulnerability': vuln
                            })

            except Exception as e:
                logger.error(f"Помилка читання requirements.txt: {e}")

        self.results['dependencies'] = deps_audit
        return deps_audit

    def generate_security_report(self) -> str:
        """Генерація звіту безпеки"""
        report = []
        report.append("# 🔒 PHASE 5: SECURITY & CONFIGURATION AUDIT REPORT")
        report.append(f"**Timestamp:** {datetime.now().isoformat()}")
        report.append("")

        # Secrets audit
        if 'secrets_audit' in self.results:
            secrets = self.results['secrets_audit']
            report.append("## 🔐 SECRETS & CREDENTIALS")
            report.append(f"- **Hardcoded secrets:** {len(secrets['hardcoded_secrets'])}")
            report.append(f"- **Environment variables used:** {len(secrets['env_variables'])}")
            report.append(f"- **Config files:** {', '.join(secrets['config_files']) if secrets['config_files'] else 'None'}")
            report.append("")

            if secrets['hardcoded_secrets']:
                report.append("### 🚨 HARDCODED SECRETS FOUND:")
                for secret in secrets['hardcoded_secrets'][:10]:  # Показати перші 10
                    report.append(f"- `{secret['file']}:{secret['line']}` - {secret['type']}")
                if len(secrets['hardcoded_secrets']) > 10:
                    report.append(f"- ... and {len(secrets['hardcoded_secrets']) - 10} more")
                report.append("")

        # Input validation
        if 'input_validation' in self.results:
            validation = self.results['input_validation']
            report.append("## 🛡️ INPUT VALIDATION")
            report.append(f"- **SQL injection risks:** {len(validation['sql_injection_risks'])}")
            report.append(f"- **Command injection risks:** {len(validation['command_injection_risks'])}")
            report.append(f"- **XSS risks:** {len(validation['xss_risks'])}")
            report.append("")

        # Error handling
        if 'error_handling' in self.results:
            errors = self.results['error_handling']
            report.append("## 🚨 ERROR HANDLING")
            report.append(f"- **Bare except blocks:** {len(errors['bare_except_blocks'])}")
            report.append(f"- **Missing exception types:** {len(errors['missing_exception_types'])}")
            report.append("")

        # Configuration
        if 'configuration' in self.results:
            config = self.results['configuration']
            report.append("## ⚙️ CONFIGURATION MANAGEMENT")
            report.append(f"- **Config files:** {len(config['config_files'])}")
            report.append(f"- **Settings files:** {len(config['settings_files'])}")
            report.append(f"- **Env files:** {len(config['env_files'])}")
            report.append(f"- **Hardcoded values:** {len(config['hardcoded_values'])}")
            report.append("")

        # Dependencies
        if 'dependencies' in self.results:
            deps = self.results['dependencies']
            report.append("## 📦 DEPENDENCIES")
            report.append(f"- **Requirements files:** {', '.join(deps['requirements_files'])}")
            report.append(f"- **Security vulnerabilities:** {len(deps['security_vulnerabilities'])}")
            report.append("")

        # Recommendations
        report.append("## 💡 SECURITY RECOMMENDATIONS")
        recommendations = self._generate_security_recommendations()
        for rec in recommendations:
            report.append(f"- {rec}")
        report.append("")

        return "\n".join(report)

    def _generate_security_recommendations(self) -> List[str]:
        """Генерація рекомендацій з безпеки"""
        recommendations = []

        if 'secrets_audit' in self.results:
            secrets = self.results['secrets_audit']
            if secrets['hardcoded_secrets']:
                recommendations.append(f"🚨 CRITICAL: Move {len(secrets['hardcoded_secrets'])} hardcoded secrets to environment variables")

        if 'input_validation' in self.results:
            validation = self.results['input_validation']
            if validation['sql_injection_risks']:
                recommendations.append(f"Fix {len(validation['sql_injection_risks'])} potential SQL injection vulnerabilities")
            if validation['command_injection_risks']:
                recommendations.append(f"Fix {len(validation['command_injection_risks'])} potential command injection vulnerabilities")

        if 'error_handling' in self.results:
            errors = self.results['error_handling']
            if errors['bare_except_blocks']:
                recommendations.append(f"Replace {len(errors['bare_except_blocks'])} bare 'except (ValueError, TypeError, ConnectionError) as e:' blocks with specific exception types")

        if 'configuration' in self.results:
            config = self.results['configuration']
            if not config['env_files']:
                recommendations.append("Create .env file for environment-specific configuration")
            if config['hardcoded_values']:
                recommendations.append(f"Move {len(config['hardcoded_values'])} hardcoded configuration values to settings")

        recommendations.extend([
            "Implement input sanitization for all user inputs",
            "Add rate limiting for API endpoints",
            "Implement proper logging for security events",
            "Regular dependency updates and security scans",
            "Use parameterized queries for all database operations"
        ])

        return recommendations

    def run_security_audit(self) -> Dict[str, Any]:
        """Запуск повного security аудиту"""
        logger.info("🚀 ЗАПУСК ФАЗИ 5: БЕЗПЕКА ТА КОНФІГУРАЦІЯ")

        results = {
            'timestamp': datetime.now().isoformat(),
            'phase': 'security_configuration_audit',
            'status': 'running'
        }

        try:
            # 1. Аудит секретів
            secrets = self.audit_secrets_and_credentials()

            # 2. Аудит валідації input
            validation = self.audit_input_validation()

            # 3. Аудит обробки помилок
            errors = self.audit_error_handling()

            # 4. Аудит конфігурації
            config = self.audit_configuration_management()

            # 5. Аудит залежностей
            deps = self.audit_dependencies()

            # 6. Генерація звіту
            report = self.generate_security_report()

            results.update({
                'secrets_audit': secrets,
                'input_validation': validation,
                'error_handling': errors,
                'configuration': config,
                'dependencies': deps,
                'report': report,
                'status': 'completed'
            })

            logger.info("✅ Фаза 5 завершена успішно")

        except Exception as e:
            logger.error(f"❌ Помилка аудиту: {e}")
            results['status'] = 'failed'
            results['error'] = str(e)

        # Збереження результатів
        self.save_results(results)

        return results

    def save_results(self, results: Dict[str, Any]) -> None:
        """Збереження результатів в файл"""
        output_file = Path('phase5_security_audit.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Результати збережено в {output_file}")

        # Також зберегти текстовий звіт
        report_file = Path('phase5_security_report.md')
        if 'report' in results:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(results['report'])

            logger.info(f"Звіт збережено в {report_file}")


def main():
    """Основна функція"""
    auditor = SecurityConfigAuditor()
    results = auditor.run_security_audit()

    # Вивід основних результатів
    print("\n" + "="*60)
    print("PHASE 5: SECURITY & CONFIGURATION AUDIT RESULTS")
    print("="*60)

    if results['status'] == 'completed':
        print("✅ Security audit completed successfully")

        if 'secrets_audit' in results:
            secrets = results['secrets_audit']
            print(f"🔐 Hardcoded secrets: {len(secrets['hardcoded_secrets'])}")
            print(f"🔧 Environment variables: {len(secrets['env_variables'])}")

        if 'input_validation' in results:
            validation = results['input_validation']
            print(f"🛡️ SQL injection risks: {len(validation['sql_injection_risks'])}")
            print(f"💉 Command injection risks: {len(validation['command_injection_risks'])}")

        if 'error_handling' in results:
            errors = results['error_handling']
            print(f"🚨 Bare except blocks: {len(errors['bare_except_blocks'])}")

    else:
        print(f"❌ Audit failed: {results.get('error', 'Unknown error')}")

    print("\n📄 Detailed results saved to phase5_security_audit.json")
    print("📄 Report saved to phase5_security_report.md")


if __name__ == "__main__":
    main()