# GitHub Org Activity Report

Generate organization-wide or user specific github metrics

---

## 🚀 Features

- **Per-user, month-by-month CSVs**:
  - PRs opened & merged, average PR size (additions/deletions)
  - Review-to-merge time (hours from first review to merge)
  - Approvals given (unique PRs per user per window)
  - Comments given on others’ PRs
  - Conversations opened (review threads started on others’ PRs)
  - Average comments received per PR
  - Top repos by activity
  - Most active hours (UTC)
- **Org-wide summary CSV**:  
  One row per user, aggregating totals and averages for the date window
- **Time-windowed**:  
  Everything filters by `--since` and `--until` (YYYY-MM-DD)
- **Fast and robust**:
  - Only pulls PRs and reviews within the window
  - Skips expensive thread queries when possible
  - Handles API rate limits and logs errors with retries

---

## 🏗️ Requirements

- Python 3.9+
- `pip install -q requests pandas python-dateutil tqdm`

---

## 🔑 Setup

1. **Generate a GitHub Personal Access Token**
   - [Create a fine-grained token here](https://github.com/settings/tokens?type=beta)
   - Scopes required:
     - `repo` (or "Repository Metadata", "Pull requests", "Contents" read)
2. **Export your token as an environment variable:**
   ```bash
   export GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxx
   ```

## 📝 Usage

Basic run for entire org:

```bash
python github_org_activity_report.py --org my-org
```

For a specific user:

```bash
python github_org_activity_report.py --org my-org --user their-github-login
```

Limit to a date window:

```bash
python github_org_activity_report.py --org my-org --since 2025-05-01 --until 2025-07-31
```

## 📄 Outputs

**Per-user CSVs:**
`<user>_<org>_summary.csv`
Each row = one month of activity for a user.

**Org-wide summary CSV:**
`<org>_orgwide_report.csv`
Each row = one user, totals/averages for the window.

## 📊 Columns Explained

| Column | Description |
|--------|-------------|
| `user`, `month` | User login, month in YYYY-MM |
| `pr_opened`, `pr_merged` | PRs opened, PRs merged |
| `avg_additions`, `avg_deletions` | Avg lines added/deleted per PR |
| `avg_merge_time_h` | Avg hours from first review to merge |
| `approvals_given` | Unique PRs user approved as a reviewer (once per PR) |
| `comments_given` | Review comments given by user on others' PRs |
| `conversations_opened` | Review threads (conversations) started as reviewer |
| `avg_comments_received_per_pr` | Avg comments received on user's own PRs |
| `top_repos` | Top 3 repos for that user/month by activity |
| `most_active_hours` | Top 3 hours (UTC) for PR/comments/reviews activity |

## ⚡️ Tips

- **Large orgs?** Use narrow date windows and/or run for a subset of users.
- **Rate limits?** Script auto-retries and sleeps if it hits GitHub API rate limits. Increase your sleep interval in run_gql if you hit problems.
- **Time zones:** All activity hours are in UTC. Add 10 for AEST, 11 for AEDT, etc.

## 🧑‍💻 Example

```bash
export GH_TOKEN=ghp_abc123...
python github_org_activity_report.py --org slypreceipts --since 2025-05-01 --until 2025-07-31
```

🙋‍♂️ Need help?
If you see “rate limit” errors, try increasing your sleep or splitting up the org into batches.
