       IDENTIFICATION DIVISION.
       PROGRAM-ID. LOANCHK.
      *================================================================*
      * LOANCHK - LOAN ACCOUNT RULES                                   *
      *   FUNCTION C: BALANCE BETWEEN -500000.00 AND 0, RATE 1%-25%    *
      *   FUNCTION R: REFINANCE ELIGIBLE WHEN STATUS OK (VIA ACCTVAL), *
      *               OWED BETWEEN 5000.00 AND 250000.00, RATE > 6%    *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY VALPARM.
       LINKAGE SECTION.
       COPY ACCTREC.
       COPY LCHKPARM.
       PROCEDURE DIVISION USING ACCT-REC LCHK-PARMS.
       0000-MAIN.
           MOVE 'Y' TO LC-RESULT
           MOVE SPACES TO LC-REASON
           EVALUATE LC-FUNCTION
               WHEN 'C'
                   PERFORM 1000-CHECK-LIMITS
               WHEN 'R'
                   PERFORM 2000-REFINANCE
               WHEN OTHER
                   MOVE 'N' TO LC-RESULT
                   MOVE 'BAD FUNCTION' TO LC-REASON
           END-EVALUATE
           GOBACK.

       1000-CHECK-LIMITS.
           IF ACCT-BALANCE > 0
               MOVE 'N' TO LC-RESULT
               MOVE 'CREDIT BALANCE' TO LC-REASON
           ELSE
               IF ACCT-BALANCE < -500000.00
                   MOVE 'N' TO LC-RESULT
                   MOVE 'OVER LOAN LIMIT' TO LC-REASON
               END-IF
           END-IF
           IF LC-RESULT = 'Y'
               IF ACCT-RATE < 0.0100 OR ACCT-RATE > 0.2500
                   MOVE 'N' TO LC-RESULT
                   MOVE 'RATE OUT OF RANGE' TO LC-REASON
               END-IF
           END-IF.

       2000-REFINANCE.
           MOVE 'S' TO VP-FUNCTION
           CALL 'ACCTVAL' USING ACCT-REC VAL-PARMS
           IF VP-RESULT NOT = 'OK'
               MOVE 'N' TO LC-RESULT
               MOVE VP-MESSAGE TO LC-REASON
           ELSE
               IF ACCT-BALANCE > -5000.00
                  OR ACCT-BALANCE < -250000.00
                   MOVE 'N' TO LC-RESULT
                   MOVE 'BALANCE NOT ELIGIBLE' TO LC-REASON
               ELSE
                   IF ACCT-RATE NOT > 0.0600
                       MOVE 'N' TO LC-RESULT
                       MOVE 'RATE ALREADY LOW' TO LC-REASON
                   END-IF
               END-IF
           END-IF.
