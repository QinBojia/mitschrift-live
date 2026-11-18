"""Convert the fp32 Parakeet encoder to fp16 (for DirectML GPUs, which have no fast int8 path).

Usage: python to_fp16.py [model_dir]   ->  <model_dir>/encoder-model.fp16.onnx (single file, ~1.2 GB)

Inputs/outputs stay float32. The small subgraph that turns the int64 `length` input into
float arithmetic (output-length formula of the subsampling convs) stays fp32 too: the generic
converter would otherwise feed fp32 casts of `length` into fp16 constants.
"""

import sys
import time
from collections import defaultdict
from pathlib import Path

import onnx
from onnx import helper
from onnxconverter_common import float16

STOP = {"Less", "Greater", "LessOrEqual", "GreaterOrEqual", "Equal"}  # bool out: the cone ends here


def length_cone(graph: onnx.GraphProto) -> set[str]:
    consumers = defaultdict(list)
    for n in graph.node:
        for i in n.input:
            consumers[i].append(n)
    producer = {o: n for n in graph.node for o in n.output}
    block, seen, todo = set(), set(), ["length"]
    while todo:
        t = todo.pop()
        for n in consumers[t]:
            if n.name in seen:
                continue
            seen.add(n.name)
            block.add(n.name)
            for i in n.input:  # keep the constants this node uses in fp32 as well
                p = producer.get(i)
                if p is not None and p.op_type == "Constant":
                    block.add(p.name)
            to = next((helper.get_attribute_value(a) for a in n.attribute if a.name == "to"), None)
            if n.op_type in STOP or (n.op_type == "Cast" and to in (6, 7, 9)):  # int32 / int64 / bool
                continue
            todo.extend(n.output)
    return block


model_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".livecapture/models/parakeet-tdt-0.6b-v3-onnx"
src, dst = model_dir / "encoder-model.onnx", model_dir / "encoder-model.fp16.onnx"
t = time.time()
m = onnx.load(str(src))  # pulls in encoder-model.onnx.data (2.4 GB)
block = length_cone(m.graph)
print(f"loaded in {time.time() - t:.0f}s, {len(m.graph.node)} nodes, {len(block)} kept in fp32 (length path)")
m16 = float16.convert_float_to_float16(m, keep_io_types=True, disable_shape_infer=True, node_block_list=sorted(block))
del m
onnx.save(m16, str(dst))
print(f"saved {dst.name}: {dst.stat().st_size / 2**20:.0f} MiB in {time.time() - t:.0f}s")
