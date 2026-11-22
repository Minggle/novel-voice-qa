# 小说精读工作区 · 《十日终焉》语音问答与测试镜像
# 运行 Web 服务: docker run -p 8080:8080 novel-tests python scripts/web.py
# 运行 CI 测试:   docker run --rm novel-tests
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir pytest edge-tts

# 创建测试结果挂载卷目录
RUN mkdir -p /results

# 复制脚本、测试用例、依赖清单与问答知识库
COPY Dockerfile /app/Dockerfile
COPY .dockerignore /app/.dockerignore
COPY docker-compose.yml /app/docker-compose.yml
COPY requirements.txt /app/requirements.txt
COPY scripts/ /app/scripts/
COPY scripts/ /app/.agents/skills/novel-companion/scripts/
COPY tests/ /app/tests/
COPY books/ /app/books/

EXPOSE 8080
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

VOLUME ["/results"]

# 默认运行全量 pytest 测试,并输出 junit xml 到 /results
CMD ["python", "-m", "pytest", "tests/", "-v", "--junitxml=/results/results.xml"]
