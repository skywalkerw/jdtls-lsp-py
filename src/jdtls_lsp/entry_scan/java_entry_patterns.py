"""行/窗口模式：供静态 ``entrypoints`` 扫描与 ``callchain-up`` 链顶启发式复用。"""

from __future__ import annotations

import re

# --- JVM / Spring Boot / Servlet (static entry scan) ---

_MAIN = re.compile(
    r"\bpublic\s+static\s+void\s+main\s*\(\s*(?:final\s+)?String\s*(?:\[\s*\w*\s*\]|\.\.\.)\s*\w+\s*\)"
)
_SPRING_BOOT_APP = re.compile(r"@SpringBootApplication\b")
_WEB_APP_INIT = re.compile(r"\bWebApplicationInitializer\b")
_SPRING_APP_RUN = re.compile(r"\bSpringApplication\s*\.\s*run\s*\(")
_WEB_SERVLET = re.compile(r"@WebServlet\s*\(")
_WEB_FILTER = re.compile(r"@WebFilter\s*\(")
_WEB_LISTENER = re.compile(r"@WebListener\b")
_HTTP_SERVLET = re.compile(r"\bextends\b[^{\n:]*\bHttpServlet\b")
_SERVLET_IFACE = re.compile(
    r"\bimplements\b[^{;]*\b(jakarta\.servlet\.Servlet|javax\.servlet\.Servlet)\b"
)
_SERVLET_CONTAINER_INIT = re.compile(r"\bimplements\b[^{;]*\bServletContainerInitializer\b")

# --- Message-oriented middleware (consumers) ---

_KAFKA_LISTENER = re.compile(r"@KafkaListener\b")
_RABBIT_LISTENER = re.compile(r"@RabbitListener\b")
_JMS_LISTENER = re.compile(r"@JmsListener\b")
_ROCKETMQ_LISTENER = re.compile(r"@RocketMQMessageListener\b")
_SQS_LISTENER = re.compile(r"@SqsListener\b")
_STREAM_LISTENER = re.compile(r"@StreamListener\b")
_PULSAR_LISTENER = re.compile(r"@PulsarListener\b")
_INCOMING_CHANNEL = re.compile(r"@Incoming\s*\(")  # MicroProfile / SmallRye reactive messaging
_SERVICE_ACTIVATOR = re.compile(r"@ServiceActivator\b")  # Spring Integration

# --- Schedulers ---

_SCHEDULED = re.compile(r"@Scheduled\b")
_SCHEDULES = re.compile(r"@Schedules\b")
_QUARTZ_EXECUTE = re.compile(
    r"\bvoid\s+execute\s*\(\s*(?:final\s+)?(?:org\.quartz\.)?JobExecutionContext\b"
)
_XXL_JOB = re.compile(r"@XxlJob\b")

# --- Spring @Async（异步执行边界；可与 @Scheduled 等同现，链顶优先级低于消息/定时） ---

_ASYNC = re.compile(r"@Async\b")

# All (kind, regex) for ``line_patterns.scan_java_entrypoints`` — order stable for tests / docs
ENTRYPOINT_LINE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("main", _MAIN),
    ("spring_boot_application", _SPRING_BOOT_APP),
    ("spring_application_run", _SPRING_APP_RUN),
    ("web_application_initializer", _WEB_APP_INIT),
    ("web_servlet", _WEB_SERVLET),
    ("web_filter", _WEB_FILTER),
    ("web_listener", _WEB_LISTENER),
    ("http_servlet", _HTTP_SERVLET),
    ("servlet", _SERVLET_IFACE),
    ("servlet_container_initializer", _SERVLET_CONTAINER_INIT),
    # messaging
    ("kafka_listener", _KAFKA_LISTENER),
    ("rabbit_listener", _RABBIT_LISTENER),
    ("jms_listener", _JMS_LISTENER),
    ("rocketmq_message_listener", _ROCKETMQ_LISTENER),
    ("sqs_listener", _SQS_LISTENER),
    ("stream_listener", _STREAM_LISTENER),
    ("pulsar_listener", _PULSAR_LISTENER),
    ("incoming_channel", _INCOMING_CHANNEL),
    ("service_activator", _SERVICE_ACTIVATOR),
    # schedulers
    ("scheduled", _SCHEDULED),
    ("schedules", _SCHEDULES),
    ("quartz_job_execute", _QUARTZ_EXECUTE),
    ("xxl_job", _XXL_JOB),
    ("spring_async", _ASYNC),
]

# Subsets for callchain-up: any match in a small source window above the method → stop tracing upward
MESSAGE_LISTENER_MATCHERS: tuple[re.Pattern[str], ...] = (
    _KAFKA_LISTENER,
    _RABBIT_LISTENER,
    _JMS_LISTENER,
    _ROCKETMQ_LISTENER,
    _SQS_LISTENER,
    _STREAM_LISTENER,
    _PULSAR_LISTENER,
    _INCOMING_CHANNEL,
    _SERVICE_ACTIVATOR,
)

SCHEDULED_TASK_MATCHERS: tuple[re.Pattern[str], ...] = (
    _SCHEDULED,
    _SCHEDULES,
    _QUARTZ_EXECUTE,
    _XXL_JOB,
)

# callchain-up：方法上方窗口内出现 @Async 则视为异步调度边界（链顶终止）
ASYNC_METHOD_MATCHERS: tuple[re.Pattern[str], ...] = (_ASYNC,)


def collect_message_listener_markers(text: str, *, limit: int = 6) -> list[str]:
    """Return human-readable markers (annotation names) found in ``text``."""
    out: list[str] = []
    seen: set[str] = set()
    for label, rx in (
        ("@KafkaListener", _KAFKA_LISTENER),
        ("@RabbitListener", _RABBIT_LISTENER),
        ("@JmsListener", _JMS_LISTENER),
        ("@RocketMQMessageListener", _ROCKETMQ_LISTENER),
        ("@SqsListener", _SQS_LISTENER),
        ("@StreamListener", _STREAM_LISTENER),
        ("@PulsarListener", _PULSAR_LISTENER),
        ("@Incoming", _INCOMING_CHANNEL),
        ("@ServiceActivator", _SERVICE_ACTIVATOR),
    ):
        if rx.search(text) and label not in seen:
            seen.add(label)
            out.append(label)
            if len(out) >= limit:
                break
    return out


def collect_async_markers(text: str) -> list[str]:
    """若窗口内存在 Spring ``@Async``，返回 ``[\"@Async\"]``。"""
    return ["@Async"] if _ASYNC.search(text) else []


def collect_scheduled_markers(text: str, *, limit: int = 4) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for label, rx in (
        ("@Scheduled", _SCHEDULED),
        ("@Schedules", _SCHEDULES),
        ("Quartz Job#execute", _QUARTZ_EXECUTE),
        ("@XxlJob", _XXL_JOB),
    ):
        if rx.search(text) and label not in seen:
            seen.add(label)
            out.append(label)
            if len(out) >= limit:
                break
    return out
