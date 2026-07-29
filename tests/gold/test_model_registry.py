"""测试模型版本管理"""
import os
import tempfile
import shutil
from backend.gold.ml.model_registry import ModelRegistry


class MockModel:
    """模拟PPOAgent的save方法"""
    def __init__(self):
        self.obs_dim = 30
        self.n_actions = 12
        self.lr = 3e-4
        self.gamma = 0.99
        self.gae_lambda = 0.95
        self.clip_epsilon = 0.2
        self.hidden_dim = 256
        self.model_type = "mlp"
        self.device = "cpu"

    def save(self, path, metrics=None):
        import json
        with open(path, "w") as f:
            json.dump({"metrics": metrics}, f)


class TestModelRegistry:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.reg = ModelRegistry(registry_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_list(self):
        model = MockModel()
        vid = self.reg.save(model, "ppo_test", {"sharpe": 1.5, "return": 10.0})
        assert vid is not None
        models = self.reg.list(sort_by="sharpe")
        assert len(models) >= 1

    def test_save_with_params(self):
        model = MockModel()
        vid = self.reg.save(model, "ppo_test", {"sharpe": 2.0},
                           params={"lr": 3e-4, "gamma": 0.99}, tags=["best", "v1"])
        info = self.reg.get(vid)
        assert info is not None
        assert info["name"] == "ppo_test"

    def test_get_best(self):
        m = MockModel()
        self.reg.save(m, "ppo_v1", {"sharpe": 1.0})
        self.reg.save(m, "ppo_v2", {"sharpe": 2.5})
        self.reg.save(m, "ppo_v3", {"sharpe": 0.5})
        best = self.reg.get_best(metric="sharpe")
        assert best["metrics"]["sharpe"] == 2.5

    def test_list_sort(self):
        m = MockModel()
        self.reg.save(m, "ppo_a", {"sharpe": 1.0, "return": 5.0})
        self.reg.save(m, "ppo_b", {"sharpe": 2.0, "return": 15.0})
        models = self.reg.list(sort_by="return", limit=5)
        assert models[0]["metrics"]["return"] >= models[-1]["metrics"]["return"]

    def test_rollback(self):
        m = MockModel()
        v1 = self.reg.save(m, "ppo", {"sharpe": 1.0})
        self.reg.save(m, "ppo", {"sharpe": 2.0})
        ok = self.reg.rollback(v1)
        assert ok
        assert self.reg._index["_current"] == v1

    def test_delete_old(self):
        m = MockModel()
        for i in range(5):
            self.reg.save(m, "ppo", {"sharpe": float(i)})
        self.reg.delete_old(max_versions=3)
        models = self.reg.list()
        assert len(models) <= 3

    def test_get_unknown_version(self):
        info = self.reg.get("nonexistent")
        assert info == {}