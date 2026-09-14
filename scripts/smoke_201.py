"""调用正在运行的 Purslyx 服务完成一遍最小 SDD 演示冒烟。

脚本只通过 HTTP 操作，不读取密码；服务进程的 PostgreSQL 连接由环境变量负责。
"""

from __future__ import annotations

import json
import os

import httpx


BASE_URL = os.getenv("PURSLYX_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def call(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, f"{BASE_URL}{path}", **kwargs)
    response.raise_for_status()
    return response.json()["data"] if path != "/health" else response.json()


def main() -> None:
    resume_text = """张三
工作经历：
负责 React 前端项目开发，使用 TypeScript 完成组件设计。
通过性能优化和自动化测试提升交付质量。"""
    job_text = """前端开发工程师
公司：示例科技
地点：杭州
任职要求：
1、熟悉 React 或 Vue 与 TypeScript
2、负责前端项目开发和交付
3、关注性能优化与自动化测试"""
    with httpx.Client(timeout=20) as client:
        health = call(client, "GET", "/health")
        resume = call(
            client,
            "POST",
            "/api/v1/demo/documents",
            json={"document_type": "resume", "title": "冒烟简历", "text": resume_text},
        )
        resume_version = call(client, "POST", f"/api/v1/demo/documents/{resume['id']}/confirm")["version"]
        job = call(
            client,
            "POST",
            "/api/v1/demo/documents",
            json={"document_type": "job", "title": "冒烟 JD", "text": job_text},
        )
        job_version = call(client, "POST", f"/api/v1/demo/documents/{job['id']}/confirm")["version"]
        report = call(
            client,
            "POST",
            "/api/v1/demo/matches",
            json={
                "resume_version_id": resume_version["id"],
                "job_version_id": job_version["id"],
            },
        )
        stored = call(client, "GET", f"/api/v1/demo/analyses/{report['id']}")
    print(
        json.dumps(
            {
                "database": health["database"],
                "environment": health["environment"],
                "analysis_status": report["status"],
                "ability_score": report["ability_score"],
                "evidence_coverage": report["evidence_coverage"],
                "stored_report_id_matches": stored["id"] == report["id"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
