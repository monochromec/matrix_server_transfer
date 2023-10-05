# matrix_server_transfer
Transfer room contents from one Matrix server to another

This small utility copies the content from one Matrix [1] server to another assuming the following
prequisites have been met:

1. Accounts with corresponding admin rights (creating rooms, etc.) have been created on both servers (named orig(in) and dest(ination) in the following),
2. The account on dest has been been invited to join orig by the account on the orig server. *This step is crucial* as it causes an OLM key transfer from orig to dest which is essential for any encrypted content to be copied,
3. Corresponding credentials are stored in a TOML file called `.server_creds.toml` (located in the user's home directory per default), filename and location can be altered via a `-c <toml_file_path>` command line parameter,
4. You need the Pypi modules `nio` and `toml` (cf. `requirements.txt`) and Python 3.10 or above.

Caveats:
- Device IDs are randomly generated (cf. the constructor of the `Matrix_Handler`class),
- As access tokens are bound to a specific device, the code prefers,
- All credentials (user names, passwords and tokens) be specified as command line parameters and take precedence over the contents of the credentials file (as per Unix default behaviour), cf. the implementation of the `Config` class,


[1] https://matrix.org