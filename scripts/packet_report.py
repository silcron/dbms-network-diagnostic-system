from __future__ import annotations

from scripts.packet_analysis import CaptureReport


def _tls_name(version: str) -> str:
    known = {"0x0304": "TLS1.3", "0x0303": "TLS1.2"}
    return known.get(version.lower(), version)


def report_lines(report: CaptureReport) -> tuple[str, ...]:
    lines = [f"capture={report.capture.name} streams={len(report.handshakes)}"]
    for handshake in report.handshakes:
        frames = ",".join(str(frame) for frame in handshake.frames)
        lines.append(
            f"handshake stream={handshake.stream} service_port={handshake.service_port} "
            f"client={handshake.client.address}:{handshake.client.port} "
            f"server={handshake.server.address}:{handshake.server.port} "
            f"frames={frames} seq_ack=valid"
        )
    for transport in report.transports:
        lines.append(
            f"transport stream={transport.stream} retransmission={transport.retransmission} "
            f"fast_retransmission={transport.fast_retransmission} "
            f"out_of_order={transport.out_of_order} lost_segment={transport.lost_segment} "
            f"reset={transport.reset}"
        )
    for diagnostic in report.diagnostics:
        lines.append(
            f"application stream={diagnostic.stream} direction={diagnostic.direction} "
            f"message={diagnostic.message_index} mode={diagnostic.mode} "
            f"status={diagnostic.status} osi_layers={diagnostic.osi_layers}"
        )
    greetings = ",".join(report.database.greetings) or "not_observed"
    lines.append(
        f"database.streams={report.database.streams} greetings={greetings} "
        f"tls_records={report.database.tls_records}"
    )
    for version in report.database.selected_versions:
        lines.append(f"tls.selected_version={_tls_name(version)}")
    if report.database.tls_records:
        lines.append("sql.visibility=opaque reason=tls_encrypted_mariadb_payload")
    lines.append("transport.application_status_independent=true")
    if report.loopback_only:
        lines.append("capture.scope=loopback physical_link_evidence=false")
    return tuple(lines)
