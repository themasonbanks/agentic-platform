"""Exercise dashboard queries and layouts against disposable synthetic services."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
GRAFANA = 'grafana/grafana@sha256:6e5d306338670f381a942f1c50d3c266bf71531e5ddd9ee4fc1a133dac5ce63d'
PROMETHEUS = 'prom/prometheus@sha256:d445d1806b38f85b5c394a1b87f94113fde0473b45245922c2e8b1bd41fa68ae'
EXPECTED = {
    'fixture/alpha': [80, 60, 120, 300, 600, 120, .6, 60, 60, 0, .25, 0, 17, 60, 5],
    'fixture/beta': [30, 90, 60, 600, 240, 60, None, 120, 0, None, None, None, 49, 0, 9],
    'fixture/(alpha|beta)': [55, 75, 180, 450, 840, 180, .6, 180, 60, 0, .25, 0, 49, 60, 9],
    'fixture/absent': [None] * 15,
}


def docker(*arguments, check=True, timeout=120):
    result = subprocess.run(['docker', *arguments], capture_output=True, text=True,
                            timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError('Docker fixture operation failed: ' + result.stderr[-2000:])
    return result.stdout.strip()


def generate(directory):
    end = int(time.time()) - 4 * 3600
    start = end - 3600
    series = []
    def counter(name, repo, rate, **labels):
        series.append((name, dict(repository=repo, **labels), rate, 1000))
    def gauge(name, repo, value):
        series.append((name, {'repository': repo}, 0, value))
    for repo, success, first, inputs, outputs, pending, lag in (
            ('fixture/alpha', 8, 6, 10, 2, 17, 5), ('fixture/beta', 3, 9, 4, 1, 49, 9)):
        counter('agentic_metric_source_events_total', repo, 10, source='ci')
        for name, wins, labels in (('agentic_runs_total', success, {}),
                                   ('agentic_ci_attempts_total', first, {'attempt': '1'})):
            counter(name, repo, wins, outcome='success', **labels)
            counter(name, repo, 10 - wins, outcome='failure', **labels)
        counter('agentic_ci_attempts_total', repo, 30, attempt='retry', outcome='success')
        counter('agentic_review_cycles_total', repo, 2 if repo.endswith('alpha') else 1)
        counter('agentic_tokens_total', repo, inputs, type='input')
        counter('agentic_tokens_total', repo, outputs, type='output')
        counter('agentic_cost_unknown_runs_total', repo, 1 if repo.endswith('alpha') else 2)
        counter('agentic_human_interventions_total', repo, 1 if repo.endswith('alpha') else 0)
        counter('agentic_deployments_total', repo, 1 if repo.endswith('alpha') else 0, outcome='failure')
        gauge('agentic_remediation_pending_age_seconds', repo, pending)
        gauge('agentic_source_lag_seconds', repo, lag)
        rates = [1, 2, 4, 4, 4] if repo.endswith('alpha') else [0, 0, 2, 4, 4]
        for bound, rate in zip(('60', '300', '600', '3600', '+Inf'), rates):
            counter('agentic_issue_to_merge_seconds_bucket', repo, rate, le=bound)
    counter('agentic_cost_usd_total', 'fixture/alpha', .01)
    counter('agentic_escaped_defects_total', 'fixture/alpha', 0)
    gauge('agentic_aws_month_cost_usd', 'fixture/alpha', .25)
    gauge('agentic_telemetry_month_cost_usd', 'fixture/alpha', 0)
    lines = []
    for name, labels, rate, base in series:
        label_text = ','.join(key + '=' + json.dumps(value) for key, value in sorted(labels.items()))
        for index in range(61):
            lines.append(f'{name}{{{label_text}}} {base + rate * index:.10g} {start + 60 * index}.0')
    (directory / 'metrics.om').write_text('\n'.join(lines) + '\n# EOF\n')
    (directory / 'prometheus.yml').write_text('global:\n  scrape_interval: 1m\nscrape_configs: []\n')
    provisioning = directory / 'provisioning'
    (provisioning / 'datasources').mkdir(parents=True)
    (provisioning / 'dashboards').mkdir()
    (provisioning / 'datasources/fixture.yml').write_text('''apiVersion: 1
datasources:
  - name: Fixture metrics
    uid: fixture-metrics
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    jsonData:
      timeInterval: 1m
''')
    (provisioning / 'dashboards/fixture.yml').write_text('''apiVersion: 1
providers:
  - name: Fixture
    orgId: 1
    type: file
    options:
      path: /fixture/dashboard
''')
    (directory / 'dashboard').mkdir()
    shutil.copyfile(ROOT / 'grafana/engineering-dashboard.json', directory / 'dashboard/dashboard.json')
    (directory / 'data').mkdir()
    directory.chmod(0o755)
    return start, end


def get(url, decode=True):
    with urlopen(url, timeout=10) as response:
        body = response.read(2_097_153)
    if len(body) > 2_097_152:
        raise ValueError('Fixture response exceeded bound')
    return json.loads(body) if decode else body


def ready(url):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            get(url, decode=False)
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError('Fixture service did not become ready')


def screenshots(url, start, end, panels, output):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for layout, viewport in [('desktop', {'width': 1440, 'height': 1400}),
                                      ('phone', {'width': 390, 'height': 844})]:
                for repo in ('fixture/alpha', 'fixture/beta'):
                    page = browser.new_page(viewport=viewport)
                    query = urlencode({'from': start * 1000, 'to': end * 1000,
                                       'var-metrics': 'fixture-metrics', 'var-repository': repo})
                    page.goto(url + '/d/agentic-engineering-draft?' + query, wait_until='networkidle')
                    for panel in panels:
                        page.get_by_text(panel['title'], exact=True).first.scroll_into_view_if_needed(timeout=30000)
                    page.get_by_text(panels[0]['title'], exact=True).first.scroll_into_view_if_needed()
                    page.screenshot(path=str(output / f'{layout}-{repo.split("/")[1]}.png'), full_page=True)
                    page.close()
        finally:
            browser.close()


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    prefix = 'dashboard-fixture-' + uuid.uuid4().hex[:10]
    containers, network_created = [], False
    temporary = tempfile.TemporaryDirectory(prefix=prefix)
    report = {'images': {'grafana': GRAFANA, 'prometheus': PROMETHEUS}, 'queries': []}
    try:
        directory = Path(temporary.name)
        start, end = generate(directory)
        report.update(start=start, end=end)
        for image in (PROMETHEUS, GRAFANA):
            docker('pull', image, timeout=240)
        user = f'{os.getuid()}:{os.getgid()}'
        common = ['--user', user, '--read-only', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--pids-limit', '256', '--cpus', '1']
        mount = ['--mount', f'type=bind,src={directory},dst=/fixture,readonly']
        data = ['--mount', f'type=bind,src={directory / "data"},dst=/prometheus']
        docker('run', '--rm', '--network', 'none', *common, '--memory', '512m', *mount, *data,
               '--entrypoint', '/bin/promtool', PROMETHEUS, 'tsdb', 'create-blocks-from',
               'openmetrics', '/fixture/metrics.om', '/prometheus')
        docker('network', 'create', '--internal', prefix)
        network_created = True
        prom_name, grafana_name = prefix + '-prometheus', prefix + '-grafana'
        containers.append(prom_name)
        docker('run', '-d', '--name', prom_name, '--network', prefix, '--network-alias', 'prometheus',
               *common, '--memory', '512m', *mount, *data, PROMETHEUS,
               '--config.file=/fixture/prometheus.yml', '--storage.tsdb.path=/prometheus')
        containers.append(grafana_name)
        docker('run', '-d', '--name', grafana_name, '--network', prefix, *common, '--memory', '1g',
               '--tmpfs', f'/var/lib/grafana:uid={os.getuid()},gid={os.getgid()}',
               '--tmpfs', '/tmp', '-p', '127.0.0.1::3000', *mount,
               '-e', 'GF_PATHS_PROVISIONING=/fixture/provisioning',
               '-e', 'GF_AUTH_ANONYMOUS_ENABLED=true', '-e', 'GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer',
               '-e', 'GF_SECURITY_ADMIN_PASSWORD=' + uuid.uuid4().hex,
               '-e', 'GF_ANALYTICS_REPORTING_ENABLED=false', '-e', 'GF_ANALYTICS_CHECK_FOR_UPDATES=false',
               '-e', 'GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false', '-e', 'GF_PLUGINS_PREINSTALL_DISABLED=true', GRAFANA)
        port = docker('port', grafana_name, '3000/tcp').rsplit(':', 1)[1]
        url = 'http://127.0.0.1:' + port
        ready(url + '/api/health')
        ready(url + '/api/dashboards/uid/agentic-engineering-draft')
        dashboard = get(url + '/api/dashboards/uid/agentic-engineering-draft')['dashboard']
        panels = sorted(dashboard['panels'], key=lambda panel: panel['id'])
        assert len(panels) == 15
        for repository, expected_values in EXPECTED.items():
            for panel, expected in zip(panels, expected_values):
                expression = panel['targets'][0]['expr'].replace('${repository:regex}', repository)
                expression = expression.replace('$__rate_interval', '5m').replace('$__range', '1h')
                query = urlencode({'query': expression, 'time': end})
                result = get(url + '/api/datasources/proxy/uid/fixture-metrics/api/v1/query?' + query)
                assert result['status'] == 'success', result
                values = result['data']['result']
                assert len(values) <= 1, (repository, panel['id'], values)
                value = float(values[0]['value'][1]) if values else None
                if value is not None and not math.isfinite(value):
                    value = None
                report['queries'].append({'repository': repository, 'panel': panel['title'],
                                           'expected': expected, 'observed': value})
                assert (value is None if expected is None else
                        value is not None and math.isclose(value, expected, rel_tol=1e-5, abs_tol=1e-5)), report['queries'][-1]
        screenshots(url, start, end, panels, output)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        for container in reversed(containers):
            docker('rm', '-f', container, check=False)
        if network_created:
            docker('network', 'rm', prefix, check=False)
        temporary.cleanup()
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--generate-only', action='store_true')
    arguments = parser.parse_args()
    if arguments.generate_only:
        arguments.output.mkdir(parents=True, exist_ok=True)
        print(json.dumps(dict(zip(('start', 'end'), generate(arguments.output)))))
    else:
        main(arguments.output)
