"""验证 scripts/ 与 .agents/skills/novel-companion/scripts/ 完全一致, 防止脚本漂移。"""
import hashlib
import os
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
AGENT_SCRIPTS_DIR = os.path.join(ROOT_DIR, ".agents", "skills", "novel-companion", "scripts")


class TestScriptSynchronization(unittest.TestCase):

    def test_scripts_match_skill_scripts_exactly(self):
        """scripts/ 与 .agents/skills/novel-companion/scripts/ 共享脚本集合与 SHA256 必须 100% 相同。"""
        self.assertTrue(os.path.isdir(SCRIPTS_DIR), f"Missing: {SCRIPTS_DIR}")
        self.assertTrue(os.path.isdir(AGENT_SCRIPTS_DIR), f"Missing: {AGENT_SCRIPTS_DIR}")

        # 收集 scripts 下的非目录文件（排除 ci/ 子目录）
        root_files = {
            f for f in os.listdir(SCRIPTS_DIR)
            if os.path.isfile(os.path.join(SCRIPTS_DIR, f)) and not f.startswith(".")
        }

        # 收集 agent scripts 下的文件
        agent_files = {
            f for f in os.listdir(AGENT_SCRIPTS_DIR)
            if os.path.isfile(os.path.join(AGENT_SCRIPTS_DIR, f)) and not f.startswith(".")
        }

        # 文件集合一致性
        self.assertEqual(root_files, agent_files, f"File set divergence! scripts: {root_files}, agent: {agent_files}")

        # SHA256 内容逐字节校验
        for fname in sorted(root_files):
            p1 = os.path.join(SCRIPTS_DIR, fname)
            p2 = os.path.join(AGENT_SCRIPTS_DIR, fname)
            with open(p1, "rb") as f1, open(p2, "rb") as f2:
                h1 = hashlib.sha256(f1.read()).hexdigest()
                h2 = hashlib.sha256(f2.read()).hexdigest()
            self.assertEqual(
                h1, h2,
                f"Script drift detected in {fname}!\nscripts: {h1}\nagent:   {h2}"
            )


if __name__ == "__main__":
    unittest.main()
