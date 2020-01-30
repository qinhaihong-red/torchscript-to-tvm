import numpy as np
import torch
import tvm
from tvm import relay

from torch_frontend import parse_script_module


class DecisionGate(torch.nn.Module):
    def forward(self, x):
        if x.sum() > 0:
            return x
        else:
            return -x


class Cell(torch.nn.Module):
    def __init__(self, dg):
        super(Cell, self).__init__()
        self.dg = dg
        self.linear = torch.nn.Linear(4, 4)

    def forward(self, x, h):
        new_h = torch.tanh(self.dg(self.linear(x)) + h)
        return new_h, new_h


class RNNLoop(torch.nn.Module):
    def __init__(self, scripted_gate):
        super().__init__()
        x = torch.rand(10, 4, dtype=torch.float)
        h = torch.rand(10, 4, dtype=torch.float)
        self.cell = torch.jit.trace(Cell(scripted_gate), (x, h))

    def forward(self, xs):
        h, y = torch.zeros(10, 4, dtype=torch.float), torch.zeros(10, 4, dtype=torch.float)
        for i in range(xs.size(0)):
            y, h = self.cell(xs[i], h)
        return y


input_name = 'X'
input_shapes = {input_name: (10, 10, 4)}

gate = DecisionGate()
models = [
    RNNLoop(gate).eval()
]

for raw_model in models:
    script_module = torch.jit.script(raw_model)
    mod, params = parse_script_module(script_module, input_shapes)

    executor = relay.create_executor("vm", mod=mod, ctx=tvm.cpu(0), target="llvm")
    evaluator = executor.evaluate()

    for i in range(5):
        inp = torch.rand(input_shapes[input_name], dtype=torch.float)

        with torch.no_grad():
            pt_result = raw_model(inp.clone())

        params[input_name] = inp.numpy()
        op_res = evaluator(**params)

        if not isinstance(pt_result, torch.Tensor):
            tvm_res = np.asscalar(op_res.asnumpy())
            print(abs(pt_result - tvm_res))
            assert pt_result == tvm_res
        else:
            print(np.max(np.abs(op_res.asnumpy() - pt_result.numpy())))
            tvm.testing.assert_allclose(op_res.asnumpy(), pt_result.numpy(),
                                        rtol=1e-5, atol=1e-5)