"""Verify one review-linked inline comment without retaining its text or author."""
import re


class InlineReviewEvidence:
    def __init__(self, api):
        self.api = api

    def __call__(self, repository, pr, review):
        if (not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)
                or len(repository) > 201
                or type(pr) is not int or not 0 < pr <= 9_223_372_036_854_775_807
                or type(review) is not int or not 0 < review <= 9_223_372_036_854_775_807):
            raise ValueError('Explicit repository and review identities required')
        comments = self.api(f'/repos/{repository}/pulls/{pr}/reviews/{review}/comments?per_page=1')
        if not isinstance(comments, list) or len(comments) > 1:
            raise ValueError('Bounded review-comment response required')
        if not comments:
            return False
        comment = comments[0]
        if not isinstance(comment, dict):
            raise ValueError('Structured review-comment metadata required')
        if (type(comment.get('id')) is not int or comment['id'] <= 0
                or type(comment.get('pull_request_review_id')) is not int
                or comment['pull_request_review_id'] != review
                or not isinstance(comment.get('path'), str) or not comment['path']):
            raise ValueError('Verified review-linked inline comment required')
        return True
