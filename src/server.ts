import "./lib/error-capture";
import { defaultStreamHandler } from "@tanstack/react-start/server";
import { getRouter } from "./router";

export default defaultStreamHandler({
  createRouter: getRouter,
});
