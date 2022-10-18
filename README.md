# volttron-lib-auth

Prototype authentication classes for volttron authentication, authorization and auth service.

## Prerequisites

* Python 3.8
* Poetry

### Python
volttron-lib-auth requires Python 3.8 or above.


To install Python 3.8, we recommend using [pyenv](https://github.com/pyenv/pyenv).

```bash
# install pyenv
git clone https://github.com/pyenv/pyenv ~/.pyenv

# setup pyenv (you should also put these three lines in .bashrc or similar)
export PATH="${HOME}/.pyenv/bin:${PATH}"
export PYENV_ROOT="${HOME}/.pyenv"
eval "$(pyenv init -)"

# install Python 3.8
pyenv install 3.8.10

# make it available globally
pyenv global system 3.8.10
```

### Poetry

This project uses `poetry` to install and manage dependencies. To install poetry,
follow these [instructions](https://python-poetry.org/docs/master/#installation).



## Installation and Virtual Environment Setup

Set the environment to be in your project directory:

```poetry config virtualenvs.in-project true```

If you want to install all your dependencies, including dependencies to help with developing your agent, run this command:

```poetry install```

If you want to install only the dependencies needed to run your agent, run this command:

```poetry install --no-dev```

Activate the virtual environment:

```poetry shell```


## Git Setup

1. To use git to manage version control, create a new git repository in your local agent project.

```
git init
```

2. Then create a new repo in your Github or Gitlab account. Copy the URL that points to that new repo in
your Github or Gitlab account. This will be known as our 'remote'.

3. Add the remote (i.e. the new repo URL from your Github or Gitlab account) to your local repository. Run the following command:

```git remote add origin <my github/gitlab URL>```

When you push to your repo, note that the default branch is called 'main'.

## Publishing to PyPi

Publishing your Agent module to PyPi is automated through the continuous integration workflow provided in `~/.github/workflows/publish_to_pypi.yml`.
You can update that Github Workflow with your credentials to ensure that publishing to PyPi will succeed. The default behavior of
that workflow is to publish to PyPi when a release has been published. If you want to change this behavior, you can modify the
workflow to publish to PyPi based on whatever desired event; see [Github Workflows docs](https://docs.github.com/en/actions/using-workflows/triggering-a-workflow)
on how to change the events that trigger a workflow.

## Optional Configurations

### Precommit

Install pre-commit hooks:

```pre-commit install```

To run pre-commit on all your files, run this command:

```pre-commit run --all-files```

If you have precommit installed and you want to ignore running the commit hooks
every time you run a commit, include the `--no-verify` flag in your commit. The following
is an example:

```git commit -m "Some message" --no-verify```

# Documentation

To build the docs, navigate to the 'docs' directory and build the documentation:

```shell
cd docs
make html
```

After the documentation is built, view the documentation in html form in your browser.
The html files will be located in `~<path to agent project directory>/docs/build/html`.

**PROTIP: To open the landing page of your documentation directly from the command line, run the following command:**

```shell
open <path to agent project directory>/docs/build/html/index.html
```

This will open the documentation landing page in your default browsert (e.g. Chrome, Firefox).
