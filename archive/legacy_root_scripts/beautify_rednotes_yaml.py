import argparse


def main() -> int:
    p = argparse.ArgumentParser(description="Beautify rednotes.yaml prompts (restore block scalar style).")
    p.add_argument("-c", "--config", default="rednotes.yaml", help="Path to rednotes.yaml")
    args = p.parse_args()

    from crawler_rednotes.config_rednotes import load_rednotes_config
    from crawler_rednotes.yaml_patch import set_block_scalar

    cfg, _raw = load_rednotes_config(args.config)

    # Only touch the prompt keys; keep everything else untouched.
    if cfg.prompt_stage1_xhs:
        set_block_scalar(args.config, ["prompt_stage1_xhs"], cfg.prompt_stage1_xhs)
    if cfg.prompt_stage2_xhs:
        set_block_scalar(args.config, ["prompt_stage2_xhs"], cfg.prompt_stage2_xhs)

    print("OK. Updated prompt fields to block scalar style.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

