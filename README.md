# Diff poetry.lock with diff-poetry-lock in CI

Poetry's TOML lockfiles are very verbose and difficult to review quickly.
This friction complicates the responsible acceptance of pull requests that change dependencies.
`diff-poetry-lock` aims to solve this problem by posting a readable summary of all lockfile changes to pull requests.

## Example

<img width="916" alt="image" src="https://user-images.githubusercontent.com/1723176/224580589-bd5e7a5f-e39f-40d3-91a2-b4bd02284100.png">

## Usage

### GitHub Actions

Simply add the following step to your Github Action:

```yaml
    steps:
      - name: Diff poetry.lock
        uses: colindean/diff-poetry-lock@main
```

When the diff changes during the lifetime of a pull request,
the original comment will be updated.
If all changes are rolled back, the comment will be deleted.

## History

* Originally written by [@nborrmann](https://github.com/nborrmann) at <https://github.com/nborrmann/diff-poetry-lock>.
* Contributions proposed to that project and unmerged as of December 2025 were integrated by
  [@banginji](https://github.com/banginji) and [@colindean](https://github.com/colindean).
