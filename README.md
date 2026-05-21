<h1 align="center"><img src="docs/assets/fox_hero_200.png" alt="ProgramBench logo" width="120"><br/>ProgramBench</h1>

<p align="center"><em>Can Language Models Rebuild Programs From Scratch?</em></p>

<p align="center">
Given only a compiled binary and its documentation, AI agents must architect and implement a complete codebase that reproduces the original program's behavior.
</p>

## Links

- [Website](https://programbench.com)
- [Paper](https://arxiv.org/abs/2605.03546)
- [HuggingFace](https://huggingface.co/datasets/programbench/ProgramBench-Tests)
- [Leaderboard](https://programbench.com)
- [Usage Guide](docs/README.md)
- [mini-swe-agent baseline](https://mini-swe-agent.com/latest/usage/programbench/)

## Quickstart

We recommend [uv](https://docs.astral.sh/uv/getting-started/installation/) for managing Python environments.

```bash
# Run without installing
uvx programbench --help

# Or install into a project
uv pip install programbench

# Or with pip
pip install programbench
```

For development:

```bash
git clone https://github.com/facebookresearch/programbench.git
cd programbench
uv sync  # installs editable + dev dependencies
```

> [!NOTE]
> For more details, please refer to the [Usage Guide](docs/README.md).

To run our baseline:

```bash
uvx --from mini-swe-agent mini-extra programbench --help
# or
pip install mini-swe-agent && mini-extra programbench --help
```

See the [mini-swe-agent baseline docs](https://mini-swe-agent.com/latest/usage/programbench/) for details.

## Citation

If our work was useful for you, please cite it:

```bibtex
@misc{yang2026programbenchlanguagemodelsrebuild,
    title={ProgramBench: Can Language Models Rebuild Programs From Scratch?},
    author={John Yang and Kilian Lieret and Jeffrey Ma and Parth Thakkar and Dmitrii Pedchenko and Sten Sootla and Emily McMilin and Pengcheng Yin and Rui Hou and Gabriel Synnaeve and Diyi Yang and Ofir Press},
    year={2026},
    eprint={2605.03546},
    archivePrefix={arXiv},
    primaryClass={cs.SE},
    url={https://arxiv.org/abs/2605.03546},
}
```

## License

ProgramBench is licensed under the terms of the license found in [LICENSE](LICENSE).
