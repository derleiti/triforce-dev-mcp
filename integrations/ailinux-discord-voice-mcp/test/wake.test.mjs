import test from "node:test";
import assert from "node:assert/strict";
import {extractWake} from "../chat_rpc.mjs";
test("Wake word triggers only for direct address",()=>{
 assert.equal(extractWake("Nova, kannst du mich hören?"),"kannst du mich hören?");
 assert.equal(extractWake("Hey Nova, wie gehts?"),"wie gehts?");
 assert.equal(extractWake("Wir reden über Nova"),null);
 assert.equal(extractWake("Nova"),"");
 assert.equal(extractWake("Brumo will Kekse"),null);
});
