#!/usr/bin/env node
import { log } from "./log.ts";
import { main } from "./main.ts";

main(process.argv.slice(2)).then(
  (code) => {
    if (code >= 0) process.exitCode = code;
  },
  (e: Error) => {
    log.error(e.message);
    if (process.env.COB_DEBUG) log.error(e.stack ?? "");
    process.exitCode = 1;
  },
);
