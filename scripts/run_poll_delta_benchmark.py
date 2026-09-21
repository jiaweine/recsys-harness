from __future__ import annotations

import argparse
import copy
import json
import math
import os
import statistics
import tempfile
import time
from pathlib import Path


def _p(values, q):
    rows=sorted(values)
    return rows[min(len(rows)-1,max(0,math.ceil(q*len(rows))-1))] if rows else 0.0


def _summary(values):
    return {
        "p50_ms": round(_p(values,0.50),4),
        "p95_ms": round(_p(values,0.95),4),
        "max_ms": round(max(values),4) if values else 0.0,
    }


def _event(index,payload_bytes):
    return {
        "phase":"execute",
        "progress":index,
        "detail":f"event-{index}",
        "payload":{"blob":"x"*payload_bytes,"index":index},
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--events",type=int,default=160)
    parser.add_argument("--payload-bytes",type=int,default=8192)
    parser.add_argument("--repeats",type=int,default=100)
    parser.add_argument("--tail",type=int,default=2)
    parser.add_argument("--min-byte-reduction-ratio",type=float,default=0.0)
    parser.add_argument("--min-encode-speedup",type=float,default=0.0)
    parser.add_argument("--max-delta-bytes",type=int,default=0)
    args=parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="xushu-run-delta-") as directory:
        os.environ["LINGJING_DATA_DIR"]=str(Path(directory))
        os.environ["LINGJING_ENV"]="development"
        import lingjing_harness.api as api

        cv=api.store.create_conversation("delta benchmark","audit")
        now=time.time()
        row={
            "run_id":"job-delta-bench",
            "conversation_id":cv["id"],
            "goal":"benchmark",
            "status":"running",
            "events":[_event(i,args.payload_bytes) for i in range(args.events)],
            "result":None,
            "attachments":[],
            "attachment_ids":[],
            "allow_network":False,
            "catalog_revision":api.CATALOG_REVISION,
            "created_at":now,
            "updated_at":now,
        }
        assert api.store.reserve_run(
            row["run_id"],cv["id"],row["goal"],row,
            owner_id=api.WORKER_ID,lease_seconds=30,
        )
        with api.RUN_LOCK:
            api.RUNS[row["run_id"]]=copy.deepcopy(row)

        full=api.get_run(row["run_id"])
        delta=api.get_run(row["run_id"],after_event=max(0,args.events-args.tail))
        full_json=json.dumps(full,ensure_ascii=False,separators=(",",":"))
        delta_json=json.dumps(delta,ensure_ascii=False,separators=(",",":"))

        full_encode=[]
        delta_encode=[]
        for _ in range(max(10,args.repeats)):
            started=time.perf_counter()
            json.dumps(full,ensure_ascii=False,separators=(",",":"))
            full_encode.append((time.perf_counter()-started)*1000)
            started=time.perf_counter()
            json.dumps(delta,ensure_ascii=False,separators=(",",":"))
            delta_encode.append((time.perf_counter()-started)*1000)

        full_bytes=len(full_json.encode("utf-8"))
        delta_bytes=len(delta_json.encode("utf-8"))
        result={
            "events":args.events,
            "tail":args.tail,
            "full_bytes":full_bytes,
            "delta_bytes":delta_bytes,
            "byte_reduction_ratio":round(full_bytes/max(delta_bytes,1),2),
            "full_encode":_summary(full_encode),
            "delta_encode":_summary(delta_encode),
            "encode_speedup_p50":round(_p(full_encode,.5)/max(_p(delta_encode,.5),1e-9),2),
        }
        print(json.dumps(result,sort_keys=True))
        failures=[]
        if args.min_byte_reduction_ratio>0 and result["byte_reduction_ratio"]<args.min_byte_reduction_ratio:
            failures.append(
                f'byte reduction={result["byte_reduction_ratio"]} < {args.min_byte_reduction_ratio}'
            )
        if args.min_encode_speedup>0 and result["encode_speedup_p50"]<args.min_encode_speedup:
            failures.append(
                f'encode speedup={result["encode_speedup_p50"]} < {args.min_encode_speedup}'
            )
        if args.max_delta_bytes>0 and result["delta_bytes"]>args.max_delta_bytes:
            failures.append(
                f'delta bytes={result["delta_bytes"]} > {args.max_delta_bytes}'
            )
        if failures:
            raise SystemExit("run poll delta performance guardrail failed: "+"; ".join(failures))


if __name__=="__main__":
    main()
