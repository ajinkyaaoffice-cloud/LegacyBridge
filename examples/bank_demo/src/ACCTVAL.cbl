       IDENTIFICATION DIVISION.
       PROGRAM-ID. ACCTVAL.
      *================================================================*
      * ACCTVAL - ACCOUNT MASTER VALIDATION                            *
      *   FUNCTION V: FULL VALIDATION (CALLS LOANCHK FOR LOANS)        *
      *   FUNCTION S: STATUS ONLY (CALLED BY LOANCHK - NO CALLS OUT)   *
      * RESULTS: OK, E1 MISSING ID, E2 BAD TYPE, E3 BAD OPEN DATE,     *
      *          E4 FROZEN, E5 CLOSED, E6 LOAN LIMITS, E7 NEGATIVE     *
      *          SAVINGS, E8 BAD STATUS, E9 BAD FUNCTION               *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY DATEPARM.
       COPY LCHKPARM.
       LINKAGE SECTION.
       COPY ACCTREC.
       COPY VALPARM.
       PROCEDURE DIVISION USING ACCT-REC VAL-PARMS.
       0000-MAIN.
           MOVE 'OK' TO VP-RESULT
           MOVE SPACES TO VP-MESSAGE
           EVALUATE VP-FUNCTION
               WHEN 'S'
                   PERFORM 2000-CHECK-STATUS
               WHEN 'V'
                   PERFORM 1000-FULL-VALIDATION
               WHEN OTHER
                   MOVE 'E9' TO VP-RESULT
                   MOVE 'INVALID FUNCTION CODE' TO VP-MESSAGE
           END-EVALUATE
           GOBACK.

       1000-FULL-VALIDATION.
           IF ACCT-ID = SPACES
               MOVE 'E1' TO VP-RESULT
               MOVE 'ACCOUNT ID MISSING' TO VP-MESSAGE
           ELSE
               IF NOT (ACCT-CHECKING OR ACCT-SAVINGS OR ACCT-LOAN)
                   MOVE 'E2' TO VP-RESULT
                   MOVE 'INVALID ACCOUNT TYPE' TO VP-MESSAGE
               END-IF
           END-IF
           IF VP-RESULT = 'OK'
               MOVE 'V' TO DP-FUNCTION
               MOVE ACCT-OPEN-DATE TO DP-DATE-1
               CALL 'DATEUTIL' USING DATE-PARMS
               IF DP-RETURN-CODE NOT = 0
                   MOVE 'E3' TO VP-RESULT
                   MOVE 'INVALID OPEN DATE' TO VP-MESSAGE
               END-IF
           END-IF
           IF VP-RESULT = 'OK'
               PERFORM 2000-CHECK-STATUS
           END-IF
           IF VP-RESULT = 'OK' AND ACCT-SAVINGS
              AND ACCT-BALANCE < 0
               MOVE 'E7' TO VP-RESULT
               MOVE 'SAVINGS BALANCE NEGATIVE' TO VP-MESSAGE
           END-IF
           IF VP-RESULT = 'OK' AND ACCT-LOAN
               MOVE 'C' TO LC-FUNCTION
               CALL 'LOANCHK' USING ACCT-REC LCHK-PARMS
               IF LC-RESULT NOT = 'Y'
                   MOVE 'E6' TO VP-RESULT
                   STRING 'LOAN: ' LC-REASON DELIMITED BY SIZE
                       INTO VP-MESSAGE
                   END-STRING
               END-IF
           END-IF.

       2000-CHECK-STATUS.
           EVALUATE TRUE
               WHEN ACCT-ACTIVE
                   CONTINUE
               WHEN ACCT-FROZEN
                   MOVE 'E4' TO VP-RESULT
                   MOVE 'ACCOUNT FROZEN' TO VP-MESSAGE
               WHEN ACCT-CLOSED
                   MOVE 'E5' TO VP-RESULT
                   MOVE 'ACCOUNT CLOSED' TO VP-MESSAGE
               WHEN OTHER
                   MOVE 'E8' TO VP-RESULT
                   MOVE 'UNKNOWN STATUS CODE' TO VP-MESSAGE
           END-EVALUATE.
